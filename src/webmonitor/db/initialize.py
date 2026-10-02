"""Single-writer initialization; incompatible schemas require explicit operator action."""
import hashlib
import os
import secrets
import stat
from importlib.resources import files

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn, CreateIndex

from webmonitor.config import get_settings
from webmonitor.db.base import Base
from webmonitor.db import models  # register every model before fingerprinting
from webmonitor.db.models.accounts import SchemaMetadata, Workspace
from webmonitor.db.models.notifications import EmailTemplate
from webmonitor.db.session import get_engine
from webmonitor.db.provision import provision_runtime
from sqlalchemy.ext.asyncio import AsyncSession
from webmonitor.services.accounts import bootstrap_admin_credentials, create_admin_in_transaction


def schema_fingerprint() -> str:
    dialect = postgresql.dialect()
    compiler = dialect.ddl_compiler(dialect, None)
    definitions = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        definitions.append(table.name)
        definitions.extend(str(CreateColumn(column).compile(dialect=dialect)) for column in table.columns)
        definitions.extend(sorted(str(CreateIndex(index).compile(dialect=dialect)) for index in table.indexes))
        definitions.extend(sorted(compiler.process(constraint) for constraint in table.constraints))
    return hashlib.sha256("\n".join(definitions).encode()).hexdigest()


def initialize_secret() -> None:
    settings = get_settings()
    path = settings.secret_file
    if path.exists():
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.geteuid() or len(stream.read(64)) < 32):
                raise RuntimeError("Signing secret requires owner-only mode 0600 and at least 32 bytes")
        return
    if not settings.development_mode:
        raise RuntimeError("Signing secret must be provisioned outside development")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return initialize_secret()
    with os.fdopen(fd, "wb") as stream:
        stream.write(secrets.token_bytes(64))


async def initialize_database() -> None:
    settings = get_settings()
    admin_credentials = bootstrap_admin_credentials(settings)
    initialize_secret()
    engine = get_engine()
    async with engine.begin() as connection:
        await connection.execute(text("SELECT pg_advisory_xact_lock(774431082)"))
        tables = await connection.run_sync(lambda c: inspect(c).get_table_names())
        metadata_name = SchemaMetadata.__tablename__
        if tables and metadata_name not in tables:
            raise RuntimeError("Existing unrecognized schema; refusing initialization")
        if metadata_name in tables:
            async with AsyncSession(bind=connection) as session:
                row = await session.scalar(select(SchemaMetadata).where(SchemaMetadata.key == "application"))
                if row is None or row.fingerprint != schema_fingerprint():
                    raise RuntimeError("Schema fingerprint mismatch; automatic migrations are disabled")
                if admin_credentials is not None:
                    workspace = await session.scalar(select(Workspace).where(Workspace.slug == "local").with_for_update())
                    if workspace is None:
                        raise RuntimeError("Local workspace missing; refusing initialization")
                    await create_admin_in_transaction(session, workspace=workspace,
                        email=admin_credentials[0], password=admin_credentials[1], allow_existing=True)
                if get_settings().bootstrap_runtime:
                    await provision_runtime(connection, get_settings())
                return
        await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(bind=connection, expire_on_commit=False) as session:
            workspace = Workspace(name="Local workspace", slug="local")
            session.add(workspace)
            resources = files("webmonitor.notifications.templates")
            for event_type, subject in {
                "price_changed": "Price changed: {{ monitor.name }}",
                "list_changed": "List changed: {{ monitor.name }}",
                "content_changed": "Content changed: {{ monitor.name }}",
                "run_failed": "Collection failed: {{ monitor.name }}",
                "recovered": "Collection recovered: {{ monitor.name }}",
            }.items():
                session.add(EmailTemplate(event_type=event_type, version=1, name=event_type,
                    subject_template=subject,
                    html_template=resources.joinpath(f"{event_type}.html").read_text(),
                    text_template=resources.joinpath(f"{event_type}.txt").read_text(),
                    is_default=True))
            session.add(SchemaMetadata(key="application", fingerprint=schema_fingerprint()))
            await session.flush()
            if admin_credentials is not None:
                await create_admin_in_transaction(session, workspace=workspace,
                    email=admin_credentials[0], password=admin_credentials[1], allow_existing=True)
        if get_settings().bootstrap_runtime:
            await provision_runtime(connection, get_settings())


async def database_ready() -> bool:
    try:
        async with AsyncSession(get_engine()) as session:
            if get_settings().require_runtime_bootstrap:
                complete = await session.scalar(text("SELECT current_setting('webmonitor.bootstrap_complete', true)"))
                if complete != "true":
                    return False
            row = await session.scalar(select(SchemaMetadata).where(SchemaMetadata.key == "application"))
            workspace = await session.scalar(select(Workspace.id).limit(1))
            return row is not None and row.initialized_at is not None and row.fingerprint == schema_fingerprint() and workspace is not None
    except Exception:
        return False
