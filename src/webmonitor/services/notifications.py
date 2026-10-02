"""Explicit notification membership; all domain writes flush, never commit."""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import AuditLog
from webmonitor.db.models.notifications import EmailTemplate, NotificationGroup, NotificationGroupMember
from webmonitor.schemas.notification_api import Group, GroupMember
from webmonitor.security.authorization import require_admin, require_admin_write, require_workspace
from webmonitor.services.drafts import revalidate_principal
from webmonitor.services.group_locks import lock_notification_groups


async def group_view(session, group):
    members = (await session.scalars(select(NotificationGroupMember).where(
        NotificationGroupMember.workspace_id == group.workspace_id,
        NotificationGroupMember.group_id == group.id,
    ).order_by(NotificationGroupMember.email))).all()
    return Group(**{key: getattr(group, key) for key in (
        "id", "name", "enabled", "deleted_at", "created_at", "updated_at")},
        members=[GroupMember.model_validate(member) for member in members])


async def get_group(session, principal, group_id: UUID, *, lock=False):
    query = select(NotificationGroup).where(NotificationGroup.id == group_id,
        NotificationGroup.workspace_id == principal.workspace_id)
    if lock:
        await lock_notification_groups(session, [group_id], exclusive=True)
        query = query.with_for_update().execution_options(populate_existing=True)
    return require_workspace(principal, await session.scalar(query))


async def _replace_members(session, group, members):
    # The caller holds the group write lock even for new member insertion. This
    # excludes routing's group shared lock, including the empty-members case.
    existing = (await session.scalars(select(NotificationGroupMember).where(
        NotificationGroupMember.workspace_id == group.workspace_id,
        NotificationGroupMember.group_id == group.id,
    ).order_by(NotificationGroupMember.id).with_for_update()
        .execution_options(populate_existing=True))).all()
    selected = {str(member.email).strip().casefold(): member.active for member in members}
    for member in existing:
        member.active = selected.pop(member.email, False)
    for email, active in selected.items():
        session.add(NotificationGroupMember(workspace_id=group.workspace_id,
            group_id=group.id, email=email, active=active))


async def write_group(session, principal, body, *, group_id=None):
    principal = await revalidate_principal(session, principal)
    if group_id is None:
        require_admin(principal)
    async with session.begin_nested():
        try:
            if group_id is None:
                group = NotificationGroup(workspace_id=principal.workspace_id,
                    created_by_user_id=principal.user_id, name=body.name.strip(), enabled=body.enabled)
                if not group.name:
                    raise DomainError("validation_failed", 422)
                session.add(group)
                await session.flush()
                # Newly inserted group is transaction-private; lock it explicitly
                # so every membership writer follows the same lock contract.
                group = await get_group(session, principal, group.id, lock=True)
            else:
                group = require_admin_write(principal, await get_group(session, principal, group_id, lock=True))
                if group.deleted_at is not None:
                    raise DomainError("group_disabled", 409)
                if body.name is not None:
                    group.name = body.name.strip()
                    if not group.name:
                        raise DomainError("validation_failed", 422)
                if body.enabled is not None:
                    group.enabled = body.enabled
            if body.members is not None:
                await _replace_members(session, group, body.members)
            session.add(AuditLog(workspace_id=principal.workspace_id, actor_user_id=principal.user_id,
                action="notification_group.updated" if group_id else "notification_group.created",
                resource_type="notification_group", resource_id=group.id,
                details={"name": group.name, "enabled": group.enabled}))
            await session.flush()
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23505":
                raise DomainError("group_name_conflict", 409) from None
            raise
    return group


async def disable_group(session, principal, group_id):
    principal = await revalidate_principal(session, principal)
    group = require_admin_write(principal, await get_group(session, principal, group_id, lock=True))
    group.enabled = False
    group.deleted_at = group.deleted_at or utc_now()
    await _replace_members(session, group, [])
    session.add(AuditLog(workspace_id=principal.workspace_id, actor_user_id=principal.user_id,
        action="notification_group.disabled", resource_type="notification_group", resource_id=group.id, details={}))
    await session.flush()
    return group


async def get_template(session, template_id):
    template = await session.scalar(select(EmailTemplate).where(EmailTemplate.id == template_id,
        EmailTemplate.is_default.is_(True)))
    if template is None:
        raise DomainError("not_found", 404)
    return template
