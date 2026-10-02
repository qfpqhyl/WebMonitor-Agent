"""Old events cannot gain new recipient authorization after source revocation."""
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Membership, User, Workspace
from webmonitor.db.models.notifications import NotificationGroup, NotificationGroupMember
from webmonitor.workers.mailer import recipient_authorized


@pytest.mark.asyncio
async def test_only_original_active_sources_authorize_delivery():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must identify an isolated initialized database")
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                async with AsyncSession(connection, expire_on_commit=False) as session:
                    suffix = uuid4().hex
                    recipient = f"{suffix}@example.com"
                    workspace = Workspace(name="Delivery authorization regression", slug=suffix)
                    user = User(email=recipient, display_name="Test", password_hash="not-a-login-hash")
                    session.add_all([workspace, user])
                    await session.flush()
                    session.add(Membership(workspace_id=workspace.id, user_id=user.id, role="admin"))
                    await session.flush()
                    groups = [NotificationGroup(workspace_id=workspace.id, name=f"Source {i}",
                                                created_by_user_id=user.id) for i in range(3)]
                    session.add_all(groups)
                    await session.flush()
                    members = [NotificationGroupMember(workspace_id=workspace.id, group_id=group.id,
                                                       email=recipient) for group in groups]
                    session.add_all(members)
                    await session.flush()
                    delivery = SimpleNamespace(workspace_id=workspace.id, recipient=recipient,
                        recipient_sources=[{"member_id": str(member.id), "group_id": str(member.group_id)}
                                           for member in members[:2]])
                    assert await recipient_authorized(session, delivery)
                    members[0].active = False
                    await session.flush()
                    assert await recipient_authorized(session, delivery)
                    groups[1].enabled = False
                    await session.flush()
                    # The third group has the same active email but was not frozen.
                    assert not await recipient_authorized(session, delivery)
                    groups[1].enabled = True
                    groups[1].deleted_at = utc_now()
                    await session.flush()
                    assert not await recipient_authorized(session, delivery)
                    groups[1].deleted_at = None
                    members[1].email = f"other-{suffix}@example.com"
                    await session.flush()
                    assert not await recipient_authorized(session, delivery)
                    # Even a new member in an original group cannot replace the
                    # frozen identity when the old member was removed or changed.
                    session.add(NotificationGroupMember(workspace_id=workspace.id,
                        group_id=groups[1].id, email=recipient))
                    await session.flush()
                    assert not await recipient_authorized(session, delivery)
                    members[0].active = True
                    await session.flush()
                    assert await recipient_authorized(session, delivery)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
