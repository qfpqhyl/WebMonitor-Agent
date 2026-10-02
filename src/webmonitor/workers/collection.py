"""Shared HTTP/browser worker loop; page IO stays outside database transactions."""
import asyncio
import contextlib
import os
import socket
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import WorkerHeartbeat, Workspace
from webmonitor.db.models.monitoring import CollectionJob, Evidence
from webmonitor.db.session import get_session_factory
from webmonitor.schemas.monitors import DraftSpec
from webmonitor.services.runs import browser_capacity_available, claim_run, commit_success, fail_run, lock_run_result, renew_lease
from webmonitor.storage.evidence import upload_evidence


async def heartbeat(worker_id,mode):
    async with get_session_factory()() as session:
        workspace_id = await session.scalar(select(Workspace.id).where(Workspace.slug == "local"))
        if workspace_id:
            statement = insert(WorkerHeartbeat).values(workspace_id=workspace_id,worker_id=worker_id,kind=mode,heartbeat_at=utc_now())
            await session.execute(statement.on_conflict_do_update(index_elements=[WorkerHeartbeat.workspace_id,WorkerHeartbeat.worker_id],set_={"heartbeat_at":utc_now(),"stopped_at":None}))
            await session.commit()


async def claim_preview(session,mode,worker_id):
    if mode == "browser" and not await browser_capacity_available(session):
        return None
    now=utc_now()
    job=await session.scalar(select(CollectionJob).where(CollectionJob.kind.in_(["preview","analyze"]),CollectionJob.collection_mode==mode,CollectionJob.available_at<=now,or_(CollectionJob.status=="queued",(CollectionJob.status=="running") & (CollectionJob.lease_expires_at<now))).order_by(CollectionJob.created_at).with_for_update(skip_locked=True).limit(1))
    if job is None:
        return None
    job.status="running"
    job.generation+=1
    job.lease_owner=worker_id
    job.lease_expires_at=now+timedelta(seconds=90)
    job.heartbeat_at=now
    job.started_at=now
    await session.flush()
    return job


async def _renew(job_id,generation,run_id,run_generation,worker_id,task):
    try:
        while True:
            await asyncio.sleep(15)
            async with get_session_factory()() as session:
                if run_id:
                    valid=await renew_lease(session,run_id,run_generation,worker_id)
                else:
                    job=await session.get(CollectionJob,job_id,with_for_update=True)
                    valid=bool(job and job.status=="running" and job.generation==generation and job.lease_owner==worker_id and job.lease_expires_at>utc_now() and not job.cancel_requested_at)
                    if valid:
                        job.heartbeat_at=utc_now()
                        job.lease_expires_at=utc_now()+timedelta(seconds=90)
                await session.commit()
            if not valid:
                task.cancel()
                return
    except Exception:
        # Without a verified renewable lease, stop the active page operation.
        task.cancel()


async def execute_job(job,run,attempt,worker_id):
    from webmonitor.collection.http import collect_http
    from webmonitor.collection.browser import collect_browser
    collector=collect_http if job.collection_mode=="http" else collect_browser
    current=asyncio.current_task()
    renewal=asyncio.create_task(_renew(job.id,job.generation,run.id if run else None,run.attempt_generation if run else None,worker_id,current))
    try:
        result=await collector(job.url,DraftSpec.model_validate(job.spec) if job.spec else None)
        uploaded=[]
        for kind,data in [("html",result.html.encode("utf-8")),("screenshot",result.screenshot)]:
            if data is not None:
                metadata=await upload_evidence(workspace_id=job.workspace_id,monitor_id=run.monitor_id if run else job.draft_id or "analysis",run_id=run.id if run else job.id,kind=kind,data=data)
                uploaded.append(metadata)
        async with get_session_factory()() as session:
            if run and not await lock_run_result(session,run.id,run.attempt_generation):
                await session.commit()
                return
            locked=await session.get(CollectionJob,job.id,with_for_update=True,populate_existing=True)
            if locked is None or locked.status!="running" or locked.generation!=job.generation or locked.lease_expires_at<=utc_now() or locked.cancel_requested_at:
                return
            references=[]
            for metadata in uploaded:
                evidence=Evidence(workspace_id=job.workspace_id,collection_job_id=job.id,run_id=run.id if run else None,attempt_id=attempt.id if attempt else None,**metadata)
                session.add(evidence)
                await session.flush()
                references.append({"id":str(evidence.id),**metadata})
            payload={"final_url":result.final_url,"fields":result.fields,"coverage":result.coverage,"evidence_refs":references,"duration_ms":result.duration_ms,"warnings":result.warnings,"analysis":result.analysis}
            if run:
                accepted=await commit_success(session,run.id,run.attempt_generation,payload)
                if not accepted:
                    await session.rollback()
                    return
            else:
                locked=await session.get(CollectionJob,job.id,with_for_update=True,populate_existing=True)
                if locked.status!="running" or locked.generation!=job.generation or locked.lease_expires_at<=utc_now() or locked.cancel_requested_at:
                    await session.rollback()
                    return
                locked.result=payload
                locked.status="succeeded"
                locked.finished_at=utc_now()
            await session.commit()
    except asyncio.CancelledError:
        async with get_session_factory()() as session:
            if run:
                await fail_run(session,run.id,run.attempt_generation,error_code="collection_cancelled",retryable=False)
            else:
                locked=await session.get(CollectionJob,job.id,with_for_update=True)
                if locked and locked.generation==job.generation and locked.status=="running":
                    locked.status="cancelled"
                    locked.finished_at=utc_now()
                    locked.error_code="collection_cancelled"
            await session.commit()
        raise
    except Exception as exc:
        code=exc.code if isinstance(exc,DomainError) else "collection_failed"
        retryable=code in {"target_unavailable","storage_unavailable","collection_timeout","browser_collection_failed"} or (code=="target_http_error" and exc.status>=500)
        failed_result=getattr(exc,"collection_result",None)
        failure_uploads=[]
        if failed_result is not None:
            try:
                for kind,data in [("html",failed_result.html.encode("utf-8")),("screenshot",failed_result.screenshot)]:
                    if data is not None:
                        failure_uploads.append(await upload_evidence(workspace_id=job.workspace_id,monitor_id=run.monitor_id if run else job.draft_id or "analysis",run_id=run.id if run else job.id,kind=kind,data=data))
            except DomainError:
                pass  # Preserve the original collection failure when evidence storage is also down.
        async with get_session_factory()() as session:
            if run and not await lock_run_result(session,run.id,run.attempt_generation):
                await session.commit()
                return
            if not run:
                await session.get(CollectionJob,job.id,with_for_update=True)
            for metadata in failure_uploads:
                session.add(Evidence(workspace_id=job.workspace_id,collection_job_id=job.id,run_id=run.id if run else None,attempt_id=attempt.id if attempt else None,**metadata))
            if run:
                await fail_run(session,run.id,run.attempt_generation,error_code=code,error_message=code.replace("_"," "),retryable=retryable,details=exc.details if isinstance(exc,DomainError) else {})
            else:
                locked=await session.get(CollectionJob,job.id,with_for_update=True)
                if locked and locked.generation==job.generation and locked.status=="running":
                    locked.status="failed"
                    locked.error_code=code
                    locked.error_message=code.replace("_"," ")
                    locked.finished_at=utc_now()
            await session.commit()
    finally:
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal


async def worker_slot(mode,worker_id):
    while True:
        async with get_session_factory()() as session:
            production=await claim_run(session,mode,worker_id)
            if production:
                run,attempt,job=production
            else:
                job=await claim_preview(session,mode,worker_id)
                run=attempt=None
            await session.commit()
        if job:
            try:
                await asyncio.create_task(execute_job(job,run,attempt,worker_id))
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
        else:
            await asyncio.sleep(0.5)


async def main(mode):
    worker_id=f"{mode}:{socket.gethostname()}:{os.getpid()}:{uuid4()}"
    async def beats():
        while True:
            await heartbeat(worker_id,mode)
            await asyncio.sleep(15)
    async with asyncio.TaskGroup() as group:
        group.create_task(beats())
        for slot in range(2 if mode=="browser" else 4):
            group.create_task(worker_slot(mode,f"{worker_id}:{slot}"))
