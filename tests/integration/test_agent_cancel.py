import os
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Workspace,User,Membership
from webmonitor.db.models.conversations import Conversation,AgentRun
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.conversations import MessageRequest
from webmonitor.services.conversations import cancel,post_message
from webmonitor.workers.agent import claim_agent_run

@pytest.mark.asyncio
async def test_cancel_releases_old_worker_lease_for_immediate_replacement(monkeypatch):
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:pytest.skip('Requires isolated initialized PostgreSQL')
    engine=create_async_engine(url)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    monkeypatch.setattr('webmonitor.workers.agent.get_session_factory',lambda:factory)
    async with factory.begin() as session:
        suffix=uuid4().hex
        workspace=Workspace(name='Cancellation boundary',slug=suffix)
        user=User(email=f'cancel-{suffix}@example.com',display_name='Test',password_hash='not-a-login-hash')
        session.add_all([workspace,user]);await session.flush()
        session.add(Membership(workspace_id=workspace.id,user_id=user.id,role='member'));await session.flush()
        conversation=Conversation(workspace_id=workspace.id,user_id=user.id,session_generation=1,session_lease_owner='old-worker',session_lease_expires_at=utc_now()+timedelta(seconds=90))
        session.add(conversation);await session.flush()
        run=AgentRun(workspace_id=workspace.id,conversation_id=conversation.id,user_id=user.id,client_message_id=uuid4(),status='running',generation=1,lease_owner='old-worker',lease_expires_at=utc_now()+timedelta(seconds=90))
        session.add(run);await session.flush()
        principal=Principal(workspace_id=workspace.id,user_id=user.id,role='member')
    try:
        async with factory.begin() as session:
            cancelled=await cancel(session,principal,conversation.id,run.id)
            assert cancelled.status=='cancelled'
            replacement=await post_message(session,principal,conversation.id,MessageRequest(content='Continue with changed requirements',client_message_id=uuid4()))
        claimed=await claim_agent_run('replacement-worker')
        assert claimed==(replacement.agent_run_id,1)
        async with factory.begin() as session:
            await cancel(session,principal,conversation.id,replacement.agent_run_id)
    finally:
        await engine.dispose()
