"""Leased at-least-once mail delivery; no SMTP I/O inside database transactions."""
import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, or_, select

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.notifications import (
    EmailDelivery, EmailTemplate, NotificationGroup, NotificationGroupMember, Outbox,
)
from webmonitor.db.session import get_session_factory
from webmonitor.notifications.delivery import SendFailure, send_email
from webmonitor.notifications.rendering import render_email
from webmonitor.workers import make_worker_id, record_heartbeat


@dataclass(frozen=True)
class Claim:
    outbox_id: UUID
    delivery_id: UUID
    generation: int
    worker_id: str


async def recipient_authorized(session, delivery) -> bool:
    """Only originally frozen member identities can authorize an old delivery."""
    identities = []
    for source in delivery.recipient_sources:
        try:
            identities.append(and_(NotificationGroupMember.id == UUID(source["member_id"]),
                                   NotificationGroupMember.group_id == UUID(source["group_id"])))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    if not identities:
        return False
    member = await session.scalar(select(NotificationGroupMember.id).join(
        NotificationGroup,
        and_(NotificationGroup.id == NotificationGroupMember.group_id,
             NotificationGroup.workspace_id == NotificationGroupMember.workspace_id),
    ).where(
        NotificationGroupMember.workspace_id == delivery.workspace_id,
        NotificationGroupMember.email == delivery.recipient,
        NotificationGroupMember.active.is_(True),
        NotificationGroup.enabled.is_(True), NotificationGroup.deleted_at.is_(None),
        or_(*identities),
    ).limit(1))
    return member is not None


def _owns(job, claim: Claim) -> bool:
    return (job is not None and job.status == "processing"
            and job.generation == claim.generation and job.lease_owner == claim.worker_id
            and job.lease_expires_at is not None and job.lease_expires_at > utc_now())


def _release(job, status: str, error: SendFailure | None = None):
    job.status = status
    job.lease_owner = None
    job.lease_expires_at = None
    job.error_code = error.code if error else None
    job.error_message = error.message if error else None
    if status != "pending":
        job.completed_at = utc_now()


async def _claim(worker_id: str, delivery_id: UUID | None) -> Claim | None:
    async with get_session_factory().begin() as session:
        now = utc_now()
        query = select(Outbox).where(
            Outbox.available_at <= now,
            or_(Outbox.status == "pending", and_(Outbox.status == "processing",
                or_(Outbox.lease_expires_at <= now, Outbox.lease_expires_at.is_(None)))),
        )
        if delivery_id is not None:
            query = query.where(Outbox.delivery_id == delivery_id)
        job = await session.scalar(query.order_by(Outbox.available_at, Outbox.id)
                                   .with_for_update(skip_locked=True).limit(1))
        if job is None:
            return None
        job.status = "processing"
        job.generation += 1
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=90)
        job.heartbeat_at = now
        return Claim(job.id, job.delivery_id, job.generation, worker_id)


async def _renew(claim: Claim, lost: asyncio.Event):
    try:
        while True:
            await asyncio.sleep(15)
            async with get_session_factory().begin() as session:
                job = await session.get(Outbox, claim.outbox_id, with_for_update=True)
                if not _owns(job, claim):
                    lost.set()
                    return
                now = utc_now()
                job.lease_expires_at = now + timedelta(seconds=90)
                job.heartbeat_at = now
                await record_heartbeat(session, "mailer", claim.worker_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Losing DB connectivity means losing proof of ownership, not permission
        # to keep sending. Never log exceptions containing infrastructure secrets.
        lost.set()


async def _load(claim: Claim):
    async with get_session_factory().begin() as session:
        job = await session.get(Outbox, claim.outbox_id, with_for_update=True)
        if not _owns(job, claim):
            return None
        delivery = await session.get(EmailDelivery, claim.delivery_id, with_for_update=True)
        if delivery.status in {"sent", "failed", "cancelled"}:
            _release(job, {"sent": "completed", "failed": "failed", "cancelled": "cancelled"}[delivery.status])
            return None
        if not await recipient_authorized(session, delivery):
            delivery.status = "cancelled"
            delivery.cancelled_at = utc_now()
            delivery.error_code = "recipient_authorization_revoked"
            delivery.error_message = "All original recipient sources were revoked"
            _release(job, "cancelled", SendFailure(delivery.error_code, delivery.error_message))
            return None
        if delivery.attempt_count >= 3:
            delivery.status = "failed"
            delivery.failed_at = utc_now()
            delivery.error_code = "smtp_attempts_exhausted"
            delivery.error_message = "SMTP attempt limit reached"
            _release(job, "failed", SendFailure(delivery.error_code, delivery.error_message))
            return None
        template = await session.get(EmailTemplate, delivery.template_id)
        event_type = template.event_type if template and template.version == delivery.template_version else None
        return delivery, event_type


async def _prepare_send(claim: Claim, rendered: dict) -> bool:
    async with get_session_factory().begin() as session:
        job = await session.get(Outbox, claim.outbox_id, with_for_update=True)
        if not _owns(job, claim):
            return False
        delivery = await session.get(EmailDelivery, claim.delivery_id, with_for_update=True)
        if not await recipient_authorized(session, delivery):
            delivery.status = "cancelled"
            delivery.cancelled_at = utc_now()
            delivery.error_code = "recipient_authorization_revoked"
            delivery.error_message = "All original recipient sources were revoked"
            _release(job, "cancelled", SendFailure(delivery.error_code, delivery.error_message))
            return False
        delivery.status = "sending"
        delivery.attempt_count += 1
        delivery.last_attempt_at = utc_now()
        delivery.rendered_subject = rendered["subject"]
        delivery.rendered_html = rendered["html"]
        delivery.rendered_text = rendered["text"]
        return True


async def _finish(claim: Claim, *, failure: SendFailure | None = None, response_code: int | None = None):
    async with get_session_factory().begin() as session:
        job = await session.get(Outbox, claim.outbox_id, with_for_update=True)
        if not _owns(job, claim):
            return
        delivery = await session.get(EmailDelivery, claim.delivery_id, with_for_update=True)
        delivery.smtp_response_code = failure.response_code if failure else response_code
        delivery.error_code = failure.code if failure else None
        delivery.error_message = failure.message if failure else None
        if failure is None:
            delivery.status = "sent"
            delivery.sent_at = utc_now()
            _release(job, "completed")
        elif failure.retryable and 0 < delivery.attempt_count < 3:
            delivery.status = "retrying"
            job.available_at = utc_now() + timedelta(seconds=60 if delivery.attempt_count == 1 else 300)
            _release(job, "pending", failure)
        else:
            delivery.status = "failed"
            delivery.failed_at = utc_now()
            _release(job, "failed", failure)


async def deliver_once(worker_id: str, *, delivery_id: UUID | None = None) -> bool:
    """Process one due job. True means claimed, not necessarily sent."""
    claim = await _claim(worker_id, delivery_id)
    if claim is None:
        return False
    lost = asyncio.Event()
    renewal = asyncio.create_task(_renew(claim, lost))
    send_task = lost_task = None
    try:
        loaded = await _load(claim)
        if loaded is None:
            return True
        delivery, event_type = loaded
        try:
            if event_type is None:
                raise DomainError("template_unavailable", 409)
            rendered = render_email(event_type, delivery.payload, version=delivery.template_version)
        except (DomainError, ValidationError, ValueError, TypeError, OSError):
            await _finish(claim, failure=SendFailure("template_contract_failed", "Frozen payload or template does not satisfy the template contract"))
            return True
        if lost.is_set() or not await _prepare_send(claim, rendered):
            return True
        send_task = asyncio.create_task(send_email(delivery, rendered))
        lost_task = asyncio.create_task(lost.wait())
        done, _ = await asyncio.wait([send_task, lost_task], return_when=asyncio.FIRST_COMPLETED)
        if lost_task in done:
            return True
        try:
            code = await send_task
        except SendFailure as failure:
            await _finish(claim, failure=failure)
        else:
            await _finish(claim, response_code=code)
        return True
    finally:
        for task in (send_task, lost_task, renewal):
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


async def run_mailer():
    worker_id = make_worker_id("mailer")
    last_heartbeat = 0.0
    loop = asyncio.get_running_loop()
    while True:
        try:
            if loop.time() - last_heartbeat >= 15:
                async with get_session_factory().begin() as session:
                    await record_heartbeat(session, "mailer", worker_id)
                last_heartbeat = loop.time()
            if not await deliver_once(worker_id):
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Failed DB writes leave the lease recoverable; no raw exception logs.
            await asyncio.sleep(5)
