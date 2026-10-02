"""Exercise real init-db in a disposable PostgreSQL schema, never shared data."""
import os
from uuid import uuid4

from argon2 import PasswordHasher
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from webmonitor.api.errors import DomainError
from webmonitor.config import Settings
from webmonitor.db import initialize
from webmonitor.db.models.accounts import AuditLog, Membership, User


@pytest_asyncio.fixture
async def isolated_initializer(monkeypatch, tmp_path):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must identify an isolated PostgreSQL database")
    schema = "admin_bootstrap_" + uuid4().hex
    control = create_async_engine(url)
    async with control.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    configured = Settings(development_mode=True, bootstrap_runtime=False,
        secret_file=tmp_path / "signing.key", admin_email=" Admin@Example.COM ",
        admin_password=SecretStr("bootstrap-password-123"))
    monkeypatch.setattr(initialize, "get_engine", lambda: engine)
    monkeypatch.setattr(initialize, "get_settings", lambda: configured)
    try:
        yield engine, configured
    finally:
        await engine.dispose()
        async with control.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await control.dispose()


@pytest.mark.asyncio
async def test_initializer_creates_admin_and_rerun_preserves_password(isolated_initializer):
    engine, configured = isolated_initializer
    await initialize.initialize_database()
    async with AsyncSession(engine) as session:
        user = await session.scalar(select(User).where(User.email == "admin@example.com"))
        user_id, original_hash = user.id, user.password_hash
        assert original_hash.startswith("$argon2id$")
        assert PasswordHasher().verify(original_hash, "bootstrap-password-123")
        membership = await session.scalar(select(Membership).where(Membership.user_id == user_id))
        assert membership.role == "admin" and membership.active and user.active
    configured.admin_password = SecretStr("different-valid-password-456")
    await initialize.initialize_database()
    async with AsyncSession(engine) as session:
        user = await session.scalar(select(User).where(User.email == "admin@example.com"))
        assert user.id == user_id and user.password_hash == original_hash
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(Membership)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "admin.created")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("conflict", ["member", "disabled_user", "disabled_membership", "missing_membership"])
async def test_initializer_rejects_conflicting_identity_without_mutation(isolated_initializer, conflict):
    engine, configured = isolated_initializer
    await initialize.initialize_database()
    async with AsyncSession(engine) as session, session.begin():
        user = await session.scalar(select(User))
        membership = await session.scalar(select(Membership))
        original_hash = user.password_hash
        if conflict == "member":
            membership.role = "member"
        elif conflict == "disabled_user":
            user.active = False
        elif conflict == "disabled_membership":
            membership.active = False
        else:
            # The audit row references membership, so remove it only in this private schema.
            await session.execute(text("DELETE FROM audit_logs"))
            await session.delete(membership)
    configured.admin_password = SecretStr("different-valid-password-456")
    with pytest.raises(DomainError) as caught:
        await initialize.initialize_database()
    assert caught.value.code == "admin_bootstrap_conflict"
    async with AsyncSession(engine) as session:
        user = await session.scalar(select(User))
        membership = await session.scalar(select(Membership))
        assert user.password_hash == original_hash
        assert user.active == (conflict != "disabled_user")
        if conflict == "missing_membership":
            assert membership is None
        else:
            assert membership.role == ("member" if conflict == "member" else "admin")
            assert membership.active == (conflict != "disabled_membership")
        assert await session.scalar(select(func.count()).select_from(User)) == 1
