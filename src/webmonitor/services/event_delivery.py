"""Freeze deduplicated recipient authorization and payload with the event transaction."""
from uuid import uuid4

from sqlalchemy import select

from webmonitor.config import get_settings
from webmonitor.db.models.notifications import (
    EmailDelivery, EmailTemplate, MonitorNotificationRoute, NotificationGroup,
    NotificationGroupMember, Outbox,
)
from webmonitor.schemas.notifications import NotificationPayload
from webmonitor.services.group_locks import lock_notification_groups


async def enqueue_event_deliveries(session, monitor, event, fields: dict) -> None:
    """Flush only. Disabled/empty routes produce no mail, never undo a valid snapshot."""
    payload = NotificationPayload.model_validate({
        "monitor": {"id": monitor.id, "name": monitor.name, "url": monitor.url},
        "event": {"id": event.id, "type": event.type, "detected_at": event.detected_at},
        "change": {"before": event.change.get("before"),
                   "after": event.change.get("after", {}),
                   "summary": event.change["summary"]},
        "fields": fields,
        "evidence_url": f"{get_settings().app_origin}/app/monitors/{monitor.id}",
    }).model_dump(mode="json")
    event.payload = payload
    routes = (await session.scalars(select(MonitorNotificationRoute).where(
        MonitorNotificationRoute.workspace_id == monitor.workspace_id,
        MonitorNotificationRoute.monitor_version_id == event.monitor_version_id,
        MonitorNotificationRoute.event_type == event.type,
    ).order_by(MonitorNotificationRoute.group_id))).all()
    await lock_notification_groups(session, [route.group_id for route in routes])
    groups = (await session.scalars(select(NotificationGroup).where(
        NotificationGroup.workspace_id == monitor.workspace_id,
        NotificationGroup.id.in_([route.group_id for route in routes]),
        NotificationGroup.enabled.is_(True), NotificationGroup.deleted_at.is_(None),
    ).order_by(NotificationGroup.id)
      .execution_options(populate_existing=True))).all()
    by_group = {route.group_id: route for route in routes}
    members = (await session.scalars(select(NotificationGroupMember).where(
        NotificationGroupMember.workspace_id == monitor.workspace_id,
        NotificationGroupMember.group_id.in_([group.id for group in groups]),
        NotificationGroupMember.active.is_(True),
    ).order_by(NotificationGroupMember.email, NotificationGroupMember.group_id)
      .execution_options(populate_existing=True))).all()
    recipients = {}
    for member in members:
        entry = recipients.setdefault(member.email, {
            "template_id": by_group[member.group_id].template_id, "sources": [],
        })
        # All groups in a version share the same event binding; reject corruption.
        if entry["template_id"] != by_group[member.group_id].template_id:
            raise ValueError("inconsistent immutable notification routes")
        entry["sources"].append({"group_id": str(member.group_id), "member_id": str(member.id)})
    templates = {template.id: template for template in (await session.scalars(
        select(EmailTemplate).where(EmailTemplate.id.in_(
            [entry["template_id"] for entry in recipients.values()])))).all()}
    for email, entry in sorted(recipients.items()):
        template = templates[entry["template_id"]]
        delivery_id = uuid4()
        session.add(EmailDelivery(id=delivery_id, workspace_id=monitor.workspace_id,
            event_id=event.id, recipient=email, channel="email", template_id=template.id,
            template_version=template.version, payload=payload,
            recipient_sources=entry["sources"], status="queued", attempt_count=0,
            message_id=f"<{delivery_id}@webmonitor.local>"))
        await session.flush()
        session.add(Outbox(workspace_id=monitor.workspace_id, event_id=event.id,
            delivery_id=delivery_id, status="pending", generation=0,
            available_at=event.detected_at))
    await session.flush()
