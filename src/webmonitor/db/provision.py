"""Trusted init-only provisioning of credentials and narrowly scoped runtime grants."""
import asyncio
import json
import os
import secrets
import subprocess
from pathlib import Path

from sqlalchemy import text

from webmonitor.config import read_credentials
from webmonitor.db.base import Base

ROLE_NAMES = {name: f"wm_{name}" for name in ("api", "agent", "scheduler", "collector", "mailer")}
IMMUTABLE = {"schema_metadata", "email_templates", "draft_revisions", "monitor_versions", "monitor_notification_routes"}
CONTROL_READ = {"workspaces", "schema_metadata", "email_templates", "monitor_versions", "monitor_notification_routes", "notification_groups", "notification_group_members", "snapshots"}


def credential_file(path: Path, values: dict[str, str]) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        return read_credentials(path, set(values))
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(values, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return read_credentials(path, set(values))


async def provision_runtime(connection, settings) -> None:
    # All DDL identifiers below come from server-owned metadata/constants. Password
    # literals are escaped, never interpolated into connection URLs or logs.
    quote = connection.dialect.identifier_preparer.quote
    async def sql(statement):
        await connection.exec_driver_sql(statement)
    async def grant(role, privileges, tables):
        if tables:
            await sql(f"GRANT {privileges} ON TABLE {', '.join('public.' + quote(t) for t in sorted(tables))} TO {quote(role)}")
    tables = set(Base.metadata.tables)
    await sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    await sql("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC")
    await sql("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC")
    await sql(f"REVOKE CREATE, TEMPORARY ON DATABASE {quote(settings.pgdatabase)} FROM PUBLIC")
    for name, role in ROLE_NAMES.items():
        credentials = credential_file(Path(f"/run/webmonitor/db/{name}/credentials.json"),
            {"username": role, "password": secrets.token_urlsafe(48)})
        if credentials["username"] != role:
            raise RuntimeError("Runtime credential identity mismatch")
        exists = await connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role})
        if exists:
            # Do not retain grants inherited from a previous/operator-defined role.
            parents = (await connection.execute(text("SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid JOIN pg_roles u ON u.oid=m.member WHERE u.rolname=:role"), {"role": role})).scalars().all()
            for parent in parents:
                await sql(f"REVOKE {quote(parent)} FROM {quote(role)}")
        else:
            await sql(f"CREATE ROLE {quote(role)}")
        password = credentials["password"].replace("'", "''")
        await sql(f"ALTER ROLE {quote(role)} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD '{password}'")
        await sql(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {quote(role)}")
        await sql(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {quote(role)}")
        await sql(f"REVOKE ALL ON SCHEMA public FROM {quote(role)}")
        await sql(f"GRANT USAGE ON SCHEMA public TO {quote(role)}")
        await sql(f"GRANT CONNECT ON DATABASE {quote(settings.pgdatabase)} TO {quote(role)}")
        await grant(role, "SELECT", {"workspaces", "schema_metadata"})
        if name == "api":
            await grant(role, "SELECT", tables)
            await grant(role, "INSERT, UPDATE, DELETE", tables - IMMUTABLE - {"workspaces"})
            await grant(role, "INSERT", {"draft_revisions", "monitor_versions", "monitor_notification_routes"})
            await sql(f"GRANT UPDATE (name) ON public.workspaces TO {quote(role)}")
        elif name == "agent":
            await grant(role, "SELECT", tables - {"users", "sessions", "invitations", "login_rate_limits"})
            await sql(f"GRANT SELECT (id, active) ON public.users TO {quote(role)}")
            writable = {"conversations", "messages", "agent_runs", "agent_checkpoints", "agent_session_items", "conversation_events", "drafts", "previews", "approvals", "idempotency_records", "monitors", "collection_jobs", "evidence", "worker_heartbeats"}
            await grant(role, "INSERT, UPDATE, DELETE", writable)
            await grant(role, "INSERT", {"draft_revisions", "monitor_versions", "monitor_notification_routes", "audit_logs"})
            await sql(f"GRANT UPDATE (updated_at) ON public.memberships TO {quote(role)}")
            await sql(f"GRANT UPDATE (name) ON public.workspaces TO {quote(role)}")
        elif name in {"collector", "scheduler"}:
            await grant(role, "SELECT", CONTROL_READ | {"monitors", "runs", "attempts", "collection_jobs", "evidence", "events", "email_deliveries", "outbox", "worker_heartbeats"})
            writable = {"runs", "attempts", "collection_jobs", "events", "email_deliveries", "outbox", "worker_heartbeats"}
            if name == "collector":
                writable |= {"evidence"}
                await grant(role, "INSERT", {"snapshots"})
            await grant(role, "INSERT, UPDATE", writable)
            columns = {"next_run_at", "health_status", "failure_fingerprint", "last_failure_at", "updated_at"}
            if name == "collector":
                columns |= {"baseline_snapshot_id", "baseline_status", "rule_state", "last_success_at"}
            await sql(f"GRANT UPDATE ({', '.join(sorted(columns))}) ON public.monitors TO {quote(role)}")
        elif name == "mailer":
            await grant(role, "SELECT", {"outbox", "email_deliveries", "notification_groups", "notification_group_members", "email_templates", "events", "worker_heartbeats"})
            await grant(role, "UPDATE", {"outbox", "email_deliveries"})
            await grant(role, "INSERT, UPDATE", {"worker_heartbeats"})
    evidence = credential_file(Path("/run/webmonitor/evidence/credentials.json"),
        {"access_key_id": "wm_evidence_" + secrets.token_hex(12), "secret_access_key": secrets.token_urlsafe(48)})
    environment = os.environ.copy()
    environment.update(S3_ENDPOINT_URL=settings.s3_endpoint_url, S3_BUCKET=settings.s3_bucket,
        S3_ACCESS_KEY_ID=evidence["access_key_id"], S3_SECRET_ACCESS_KEY=evidence["secret_access_key"])
    def provision_storage():
        result = subprocess.run(["/usr/local/bin/python", "/opt/webmonitor/provision_s3.py"],
            env=environment, capture_output=True, timeout=360, check=False)
        if result.returncode:
            raise RuntimeError("Restricted S3 runtime provisioning failed")
    await asyncio.to_thread(provision_storage)
    # This login setting commits atomically with schema/role grants only after S3
    # succeeds. No model/fingerprint change is needed for deployment readiness.
    await sql(f"ALTER ROLE wm_api IN DATABASE {quote(settings.pgdatabase)} SET webmonitor.bootstrap_complete = 'true'")
