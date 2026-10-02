"""One fresh deterministic notification contract for preview, confirm and create."""
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webmonitor.api.errors import DomainError
from webmonitor.db.models.notifications import EmailTemplate, NotificationGroup, NotificationGroupMember
from webmonitor.schemas.monitors import DraftSpec, content_hash
from webmonitor.services.group_locks import lock_notification_groups


@dataclass(frozen=True)
class RoutingSnapshot:
    recipient_snapshot: list[dict[str, Any]]
    template_snapshot: list[dict[str, Any]]


async def load_routing_snapshot(
    session: AsyncSession, workspace_id: UUID, spec: DraftSpec,
) -> RoutingSnapshot:
    """Hold shared advisory group locks; edits take the matching exclusive lock.

    Source membership IDs are frozen so later delivery can recheck authorization.
    Templates are server-owned immutable defaults, never user-provided code.
    """
    await lock_notification_groups(session, spec.notification_group_ids)
    groups = (await session.scalars(select(NotificationGroup).where(
        NotificationGroup.workspace_id == workspace_id,
        NotificationGroup.id.in_(spec.notification_group_ids),
        NotificationGroup.enabled.is_(True), NotificationGroup.deleted_at.is_(None),
    ).order_by(NotificationGroup.id)
        .execution_options(populate_existing=True))).all()
    if len(groups) != len(spec.notification_group_ids):
        raise DomainError("notification_routes_invalid", 409)
    members = (await session.scalars(select(NotificationGroupMember).where(
        NotificationGroupMember.workspace_id == workspace_id,
        NotificationGroupMember.group_id.in_([group.id for group in groups]),
        NotificationGroupMember.active.is_(True),
    ).order_by(NotificationGroupMember.email, NotificationGroupMember.group_id, NotificationGroupMember.id)
        .execution_options(populate_existing=True))).all()
    recipients: dict[str, list[dict[str, str]]] = {}
    for member in members:
        recipients.setdefault(member.email, []).append({
            "group_id": str(member.group_id), "member_id": str(member.id),
        })
    if not recipients:
        raise DomainError("notification_recipients_empty", 409)
    templates = (await session.scalars(select(EmailTemplate).where(
        EmailTemplate.id.in_([binding.template_id for binding in spec.template_bindings.values()]),
        EmailTemplate.is_default.is_(True),
    ).order_by(EmailTemplate.event_type)
        .execution_options(populate_existing=True))).all()
    by_id = {template.id: template for template in templates}
    template_snapshot = []
    for event_type, binding in sorted(spec.template_bindings.items()):
        template = by_id.get(binding.template_id)
        if template is None or template.event_type != event_type or template.version != binding.version:
            raise DomainError("template_binding_invalid", 409)
        template_snapshot.append({
            "event_type": event_type, "template_id": str(template.id), "version": template.version,
            "content_hash": content_hash({"subject": template.subject_template,
                "html": template.html_template, "text": template.text_template}),
        })
    return RoutingSnapshot(
        recipient_snapshot=[{"email": email, "sources": sources} for email, sources in sorted(recipients.items())],
        template_snapshot=template_snapshot,
    )
