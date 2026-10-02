"""Preview jobs share collectors but cannot write a production baseline or outbox."""
import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from webmonitor.api.errors import DomainError
from webmonitor.config import get_settings
from webmonitor.db.base import utc_now
from webmonitor.db.models.conversations import Draft, DraftRevision, Preview
from webmonitor.db.models.monitoring import CollectionJob, Evidence
from webmonitor.db.session import get_session_factory
from webmonitor.notifications.rendering import render_email
from webmonitor.schemas.monitors import DraftSpec, content_hash
from webmonitor.security.egress import validate_target
from webmonitor.services.approvals import compute_preview_hash
from webmonitor.services.drafts import revalidate_principal, require_draft_owner
from webmonitor.services.routing import load_routing_snapshot
from webmonitor.collection.diff import evaluate_rules


async def enqueue_collection(session,principal,*,url:str,mode:str,spec:DraftSpec|None=None,draft_id:UUID|None=None,revision:int|None=None,agent_run_id:UUID|None=None):
    principal = await revalidate_principal(session,principal)
    url = await validate_target(url)
    if mode not in {"http","browser"}:
        raise DomainError("validation_failed",422)
    if spec is not None:
        draft = await session.scalar(select(Draft).where(Draft.id == draft_id,Draft.workspace_id == principal.workspace_id).with_for_update())
        require_draft_owner(principal,draft)
        if draft.current_revision != revision:
            raise DomainError("stale_revision",409)
        stored = await session.scalar(select(DraftRevision).where(DraftRevision.draft_id == draft_id,DraftRevision.revision == revision))
        if stored is None or content_hash(spec) != stored.spec_hash:
            raise DomainError("stale_revision",409)
        active = await session.scalar(select(CollectionJob).where(CollectionJob.draft_id == draft_id,CollectionJob.revision == revision,CollectionJob.kind == "preview",CollectionJob.status.in_(["queued","running"])))
        if active:
            return active
    job = CollectionJob(workspace_id=principal.workspace_id,kind="preview" if spec else "analyze",collection_mode=mode,
        url=url,spec=spec.model_dump(mode="json") if spec else None,requested_by_user_id=principal.user_id,
        draft_id=draft_id,revision=revision,agent_run_id=agent_run_id)
    session.add(job)
    await session.flush()
    return job


async def wait_for_collection(job_id,principal,*,timeout=75):
    async with asyncio.timeout(timeout):
        while True:
            async with get_session_factory()() as session:
                await revalidate_principal(session,principal)
                job = await session.scalar(select(CollectionJob).where(CollectionJob.id == job_id,CollectionJob.workspace_id == principal.workspace_id))
                if job is None:
                    raise DomainError("not_found",404)
                if job.status == "succeeded":
                    return job.result
                if job.status in {"failed","cancelled","discarded"}:
                    raise DomainError(job.error_code or "collection_cancelled",409)
            await asyncio.sleep(0.25)


async def build_preview(session,principal,*,draft_id,revision,job_id):
    principal = await revalidate_principal(session,principal)
    draft = await session.scalar(select(Draft).where(Draft.id == draft_id,Draft.workspace_id == principal.workspace_id).with_for_update())
    require_draft_owner(principal,draft)
    if draft.current_revision != revision:
        raise DomainError("stale_revision",409)
    stored = await session.scalar(select(DraftRevision).where(DraftRevision.draft_id == draft_id,DraftRevision.revision == revision))
    job = await session.scalar(select(CollectionJob).where(CollectionJob.id == job_id,CollectionJob.workspace_id == principal.workspace_id))
    if job is None or job.kind != "preview" or job.draft_id != draft_id or job.revision != revision or job.status != "succeeded" or job.result is None:
        raise DomainError("preview_not_ready",409)
    spec = DraftSpec.model_validate(stored.spec)
    result = job.result
    routes = await load_routing_snapshot(session,principal.workspace_id,spec)
    fields = result["fields"]
    simulation = evaluate_rules(spec,None,fields,{})
    types = {"run_failed","recovered"}
    fields_by_name = {field.name:field for field in spec.fields}
    for rule in spec.change_rules.rules:
        field = fields_by_name[rule.field]
        types.add("price_changed" if field.type == "money" else "list_changed" if field.type == "list" else "content_changed")
    rendered = []
    preview_id = uuid4()
    for event_type in sorted(types):
        payload = {"monitor":{"id":draft.id,"name":spec.name,"url":spec.url},"event":{"id":preview_id,"type":event_type,"detected_at":utc_now()},"change":{"before":None,"after":fields,"summary":"Preview only. First successful production collection establishes the baseline without a business notification."},"fields":fields,"evidence_url":get_settings().app_origin+f"/app?draft_id={draft.id}"}
        rendered.append({"event_type":event_type,**render_email(event_type,payload,version=spec.template_bindings[event_type].version)})
    data = {"spec_hash":stored.spec_hash,"extracted_data":fields,"coverage":result["coverage"],"evidence_refs":result["evidence_refs"],"rule_simulation":simulation,"rendered_emails":rendered,"recipient_snapshot":routes.recipient_snapshot,"template_snapshot":routes.template_snapshot}
    now = utc_now()
    preview = Preview(id=preview_id,workspace_id=principal.workspace_id,user_id=principal.user_id,draft_id=draft.id,revision=revision,collection_job_id=job.id,created_at=now,expires_at=now+timedelta(minutes=15),preview_hash=compute_preview_hash(**data),warnings=result.get("warnings",[]),**data)
    session.add(preview)
    await session.flush()
    for evidence in (await session.scalars(select(Evidence).where(Evidence.collection_job_id == job.id))).all():
        evidence.preview_id = preview.id
    await session.flush()
    return preview
