"""Expired/replaced workers and failed extraction cannot overwrite trusted data."""
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from webmonitor.config import get_settings
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Membership, User, Workspace
from webmonitor.db.models.conversations import Conversation, Draft, Preview
from webmonitor.db.models.monitoring import CollectionJob, Evidence, Event, Monitor, Snapshot
from webmonitor.db.models.notifications import EmailTemplate, NotificationGroup, NotificationGroupMember
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.services import runs
from webmonitor.services.approvals import approval_token, compute_preview_hash, confirm_preview
from webmonitor.services.drafts import save_monitor_draft
from webmonitor.services.monitors import create_monitor_task
from webmonitor.services.routing import load_routing_snapshot


async def monitor_fixture(session):
    suffix = uuid4().hex
    workspace = Workspace(name="Run boundary regression", slug=suffix)
    user = User(email=f"{suffix}@example.com", display_name="Test", password_hash="not-a-login-hash")
    session.add_all([workspace, user])
    await session.flush()
    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="admin"))
    await session.flush()
    principal = Principal(workspace_id=workspace.id, user_id=user.id, role="admin")
    conversation = Conversation(workspace_id=workspace.id, user_id=user.id)
    group = NotificationGroup(workspace_id=workspace.id, name="Boundary recipient", created_by_user_id=user.id)
    session.add_all([conversation, group])
    await session.flush()
    session.add(NotificationGroupMember(workspace_id=workspace.id, group_id=group.id, email=user.email))
    await session.flush()
    templates = (await session.scalars(select(EmailTemplate))).all()
    spec = DraftSpec.model_validate({
        "name": "Heading", "url": "https://8.8.8.8/", "collection_mode": "http",
        "fields": [{"name": "title", "type": "text", "semantic": "Heading", "selector": "h1"}],
        "change_rules": {"mode": "any", "rules": [{"type": "text_changed", "field": "title"}]},
        "schedule": {"interval_seconds": 60, "timezone": "UTC"},
        "notification_group_ids": [str(group.id)],
        "template_bindings": {t.event_type: {"template_id": str(t.id), "version": t.version} for t in templates},
        "coverage": {"scope": "full", "description": "Single heading"}})
    revision = await save_monitor_draft(session, principal, conversation_id=conversation.id, spec=spec)
    draft = await session.get(Draft, revision.draft_id)
    job = CollectionJob(workspace_id=workspace.id, kind="preview", collection_mode="http", status="succeeded",
                        draft_id=draft.id, revision=revision.revision, url=spec.url, spec=revision.spec,
                        requested_by_user_id=user.id)
    session.add(job)
    await session.flush()
    routing = await load_routing_snapshot(session, workspace.id, spec)
    data = {"spec_hash": revision.spec_hash, "extracted_data": {"title": "trusted"},
            "coverage": spec.coverage.model_dump(), "evidence_refs": [], "rule_simulation": {},
            "rendered_emails": [], "recipient_snapshot": routing.recipient_snapshot,
            "template_snapshot": routing.template_snapshot}
    preview = Preview(workspace_id=workspace.id, draft_id=draft.id, revision=revision.revision,
                      collection_job_id=job.id, user_id=user.id, preview_hash=compute_preview_hash(**data),
                      expires_at=utc_now() + timedelta(minutes=15), **data)
    session.add(preview)
    await session.flush()
    approval = await confirm_preview(session, principal, draft_id=draft.id,
                                     revision=revision.revision, preview_id=preview.id)
    created = await create_monitor_task(session, principal, draft_id=draft.id,
        confirmed_revision=revision.revision, confirmation_token=approval_token(approval), idempotency_key=suffix)
    monitor = await session.get(Monitor, created.task_id)
    run = await runs.enqueue_manual(session, principal, monitor.id)
    claimed, attempt, collection_job = await runs.claim_run(session, "http", "boundary-worker")
    assert claimed.id == run.id
    evidence = Evidence(workspace_id=workspace.id, collection_job_id=collection_job.id,
        run_id=run.id, attempt_id=attempt.id, kind="html", bucket="test-boundary",
        object_key=suffix, content_type="text/html", size_bytes=1, sha256="0" * 64)
    session.add(evidence)
    await session.flush()
    result = {"fields": {"title": "trusted"}, "coverage": spec.coverage.model_dump(),
              "evidence_refs": [{"id": str(evidence.id), "kind": "html", "content_type": "text/html", "size_bytes": 1}],
              "final_url": spec.url, "warnings": []}
    assert await runs.commit_success(session, run.id, run.attempt_generation, result)
    return principal, monitor, result


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["generation", "expired", "paused", "failure"])
async def test_run_boundary_preserves_last_success(boundary, monkeypatch, tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Use docker compose --profile dev run --rm dev pytest for an isolated initialized test DB")
    secret = tmp_path / "signing.key"
    secret.write_bytes(os.urandom(64))
    secret.chmod(0o600)
    monkeypatch.setenv("SECRET_FILE", str(secret))
    monkeypatch.setenv("DEVELOPMENT_MODE", "true")
    get_settings.cache_clear()
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(connection, expire_on_commit=False) as session:
                    principal, monitor, result = await monitor_fixture(session)
                    baseline_id = monitor.baseline_snapshot_id
                    trusted = await session.get(Snapshot, baseline_id)
                    assert trusted.extracted_data == {"title": "trusted"}
                    run = await runs.enqueue_manual(session, principal, monitor.id)
                    claimed, _, _ = await runs.claim_run(session, "http", "new-worker")
                    assert claimed.id == run.id
                    generation = run.attempt_generation
                    if boundary == "generation":
                        generation -= 1
                    elif boundary == "expired":
                        run.lease_expires_at = utc_now() - timedelta(seconds=1)
                    elif boundary == "paused":
                        await runs.pause_monitor(session, principal, monitor.id)
                    await session.flush()
                    if boundary == "failure":
                        assert await runs.fail_run(session, run.id, generation,
                            error_code="extraction_invalid", retryable=True)
                        assert run.status == "failed"
                        events = (await session.scalars(select(Event).where(Event.run_id == run.id))).all()
                        assert [event.type for event in events] == ["run_failed"]
                    else:
                        changed = dict(result, fields={"title": "untrusted"})
                        assert not await runs.commit_success(session, run.id, generation, changed)
                        assert not await runs.fail_run(session, run.id, generation, error_code="target_unavailable")
                        assert not (await session.scalars(select(Event).where(Event.run_id == run.id))).all()
                    assert monitor.baseline_snapshot_id == baseline_id
                    assert (await session.get(Snapshot, baseline_id)).extracted_data == {"title": "trusted"}
                    assert await session.scalar(select(Snapshot.id).where(Snapshot.run_id == run.id)) is None
            finally:
                await transaction.rollback()
    finally:
        get_settings.cache_clear()
        await engine.dispose()
