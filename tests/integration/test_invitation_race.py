"""Concurrent consumption must create one membership, never two."""
import asyncio
import hashlib
import os
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Invitation, Membership, User, Workspace


@pytest.mark.asyncio
async def test_single_invitation_consumption():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must point to an initialized isolated PostgreSQL database")
    from webmonitor.services.accounts import register
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid4().hex
    token = secrets.token_urlsafe(32)
    async with sessions.begin() as session:
        workspace = Workspace(name="Isolated invitation race", slug=f"race-{suffix}")
        administrator = User(email=f"admin-{suffix}@example.com", display_name="Test", password_hash="not-a-login-hash")
        session.add_all([workspace, administrator])
        await session.flush()
        session.add(Membership(workspace_id=workspace.id, user_id=administrator.id, role="admin"))
        await session.flush()
        session.add(Invitation(workspace_id=workspace.id, email=f"member-{suffix}@example.com", token_hash=hashlib.sha256(token.encode()).hexdigest(), created_by_user_id=administrator.id, expires_at=utc_now()+timedelta(minutes=5)))
    async def consume():
        async with sessions() as session:
            try:
                await register(session, display_name="Concurrent member", email=f"member-{suffix}@example.com", password="valid-test-password-123", invitation_token=token)
                return "created"
            except DomainError as exc:
                return exc.code
    try:
        outcomes = await asyncio.gather(consume(), consume())
        assert sorted(outcomes) == ["created", "registration_failed"]
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(Membership).where(Membership.workspace_id == workspace.id, Membership.role == "member")) == 1
    finally:
        await engine.dispose()
