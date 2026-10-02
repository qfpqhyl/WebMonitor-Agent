"""Chat Completions synthetic IDs must not collapse distinct persisted history."""
import os
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from webmonitor.agent.session import PostgreSQLSession
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Workspace,User,Membership
from webmonitor.db.models.conversations import Conversation,AgentRun

@pytest.mark.asyncio
async def test_synthetic_ids_preserve_messages_and_tool_pair(monkeypatch):
    url=os.environ.get("TEST_DATABASE_URL")
    if not url:pytest.skip("Requires isolated initialized PostgreSQL")
    engine=create_async_engine(url)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr("webmonitor.agent.session.get_session_factory",lambda:factory)
    worker="sdk-history-test"
    async with factory.begin() as session:
        suffix=uuid4().hex
        workspace=Workspace(name="History boundary",slug=suffix)
        user=User(email=f"history-{suffix}@example.com",display_name="Test",password_hash="not-a-login-hash")
        session.add_all([workspace,user]);await session.flush()
        session.add(Membership(workspace_id=workspace.id,user_id=user.id,role="member"));await session.flush()
        conversation=Conversation(workspace_id=workspace.id,user_id=user.id,session_lease_owner=worker,session_generation=1,session_lease_expires_at=utc_now()+timedelta(seconds=90))
        session.add(conversation);await session.flush()
        run=AgentRun(workspace_id=workspace.id,conversation_id=conversation.id,user_id=user.id,client_message_id=uuid4(),status="running",generation=1,lease_owner=worker,lease_expires_at=utc_now()+timedelta(seconds=90))
        session.add(run);await session.flush()
    adapter=PostgreSQLSession(conversation.id,run.id,1,worker)
    items=[{"id":"__fake_id__","type":"message","role":"assistant","content":"First explanation"},{"id":"__fake_id__","type":"message","role":"assistant","content":"Second explanation"},{"id":"__fake_id__","type":"function_call","call_id":"call-123","name":"preview_monitor","arguments":"{}"},{"type":"function_call_output","call_id":"call-123","output":"real-result"}]
    try:
        await adapter.add_items(items)
        await adapter.add_items(items)
        assert await adapter.get_items()==items
        assert await adapter.pop_item()==items[-1]
        assert await adapter.get_items()==items[:-1]
    finally:
        await engine.dispose()
