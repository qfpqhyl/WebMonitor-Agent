"""Changing a draft invalidates a previously signed approval and its preview."""
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from webmonitor.api.errors import DomainError
from webmonitor.config import get_settings
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Membership, User, Workspace
from webmonitor.db.models.conversations import Conversation, Draft, Preview
from webmonitor.db.models.monitoring import CollectionJob
from webmonitor.db.models.notifications import EmailTemplate, NotificationGroup, NotificationGroupMember
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.services.approvals import approval_token, compute_preview_hash, confirm_preview
from webmonitor.services.drafts import save_monitor_draft
from webmonitor.services.routing import load_routing_snapshot
from webmonitor.services.monitors import create_monitor_task


@pytest.mark.asyncio
@pytest.mark.parametrize("change,expected", [("revision","stale_revision"),("expiry","preview_expired"),("membership","unauthenticated"),("consumed","confirmation_required")])
async def test_invalidated_authority_rejects_old_approval(monkeypatch, tmp_path, change, expected):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL must identify an isolated initialized database")
    secret = tmp_path / "key"
    secret.write_bytes(os.urandom(64))
    secret.chmod(0o600)
    monkeypatch.setenv("SECRET_FILE", str(secret))
    monkeypatch.setenv("DEVELOPMENT_MODE", "true")
    get_settings.cache_clear()
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions.begin() as session:
            suffix = uuid4().hex
            workspace = Workspace(name="Approval revision test", slug=suffix)
            user = User(email=f"{suffix}@example.com", display_name="Test", password_hash="not-a-login-hash")
            session.add_all([workspace, user]); await session.flush()
            session.add(Membership(workspace_id=workspace.id,user_id=user.id,role="admin")); await session.flush()
            principal = Principal(workspace_id=workspace.id,user_id=user.id,role="admin")
            conversation = Conversation(workspace_id=workspace.id,user_id=user.id)
            group = NotificationGroup(workspace_id=workspace.id,name="Test recipient",created_by_user_id=user.id)
            session.add_all([conversation,group]); await session.flush()
            session.add(NotificationGroupMember(workspace_id=workspace.id,group_id=group.id,email=f"{suffix}@example.com")); await session.flush()
            templates = (await session.scalars(select(EmailTemplate))).all()
            spec = DraftSpec.model_validate({"name":"Text","url":"https://example.com/","collection_mode":"http","fields":[{"name":"title","type":"text","semantic":"Title","selector":"h1"}],"change_rules":{"mode":"any","rules":[{"type":"text_changed","field":"title"}]},"schedule":{"interval_seconds":60,"timezone":"UTC"},"notification_group_ids":[str(group.id)],"template_bindings":{t.event_type:{"template_id":str(t.id),"version":t.version} for t in templates},"coverage":{"scope":"full","description":"One heading"}})
            revision = await save_monitor_draft(session,principal,conversation_id=conversation.id,spec=spec)
            draft = await session.get(Draft,revision.draft_id)
            job = CollectionJob(workspace_id=workspace.id,kind="preview",collection_mode="http",status="succeeded",draft_id=draft.id,revision=revision.revision,url=spec.url,spec=revision.spec,requested_by_user_id=user.id)
            session.add(job); await session.flush()
            routes = await load_routing_snapshot(session,workspace.id,spec)
            data = {"spec_hash":revision.spec_hash,"extracted_data":{"title":"Before"},"coverage":spec.coverage.model_dump(),"evidence_refs":[],"rule_simulation":{},"rendered_emails":[],"recipient_snapshot":routes.recipient_snapshot,"template_snapshot":routes.template_snapshot}
            preview = Preview(workspace_id=workspace.id,draft_id=draft.id,revision=revision.revision,collection_job_id=job.id,user_id=user.id,preview_hash=compute_preview_hash(**data),expires_at=utc_now()+timedelta(minutes=15),**data)
            session.add(preview); await session.flush()
            approval = await confirm_preview(session,principal,draft_id=draft.id,revision=revision.revision,preview_id=preview.id)
            token = approval_token(approval)
            if change == "revision":
                revised_spec = spec.model_copy(update={"name":"Changed"})
                revised = await save_monitor_draft(session,principal,conversation_id=conversation.id,spec=revised_spec,draft_id=draft.id)
                assert revised.revision == revision.revision+1
            elif change == "expiry":
                preview.expires_at = utc_now()-timedelta(seconds=1)
            elif change == "membership":
                membership = await session.scalar(select(Membership).where(Membership.workspace_id==workspace.id,Membership.user_id==user.id))
                membership.active = False
            else:
                approval.consumed_at = utc_now()
            await session.flush()
            with pytest.raises(DomainError) as exc:
                await create_monitor_task(session,principal,draft_id=draft.id,confirmed_revision=revision.revision,confirmation_token=token,idempotency_key=str(uuid4()))
            assert exc.value.code == expected
    finally:
        get_settings.cache_clear()
        await engine.dispose()
