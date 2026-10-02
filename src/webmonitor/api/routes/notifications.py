"""Workspace notification resources and version-bound route confirmation."""
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from webmonitor.api.dependencies import current_principal
from webmonitor.api.errors import DomainError
from webmonitor.api.pagination import paginate
from webmonitor.db.models.monitoring import CollectionJob
from webmonitor.db.models.notifications import EmailDelivery, EmailTemplate, NotificationGroup
from webmonitor.db.session import session_dependency
from webmonitor.notifications.rendering import render_email
from webmonitor.schemas.identity import Principal
from webmonitor.schemas.notification_api import (
    Delivery, Group, GroupPatch, Page, PreviewJobStatus, RenderedEmail, RouteConfirmRequest,
    RoutePreview, RoutePreviewFinish, RoutePreviewJob, RoutePreviewRequest, RouteUpdate, RouteView, Template,
)
from webmonitor.schemas.notifications import NotificationGroupSpec, NotificationPayload
from webmonitor.services import notification_routes as routes
from webmonitor.services.notifications import disable_group, get_group, get_template, group_view, write_group

router = APIRouter(prefix="/api/v1", tags=["notifications"])


@router.get("/notification-groups", response_model=Page[Group])
async def list_groups(cursor: str | None = None, limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    rows, next_cursor = await paginate(session, select(NotificationGroup).where(
        NotificationGroup.workspace_id == principal.workspace_id,
        NotificationGroup.deleted_at.is_(None)), NotificationGroup, cursor, limit)
    return Page[Group](items=[await group_view(session, row) for row in rows], next_cursor=next_cursor)


@router.get("/notification-groups/{group_id}", response_model=Group)
async def read_group(group_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    return await group_view(session, await get_group(session, principal, group_id))


@router.post("/notification-groups", response_model=Group, status_code=201)
async def create_group(body: NotificationGroupSpec, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    group = await write_group(session, principal, body)
    result = await group_view(session, group)
    await session.commit()
    return result


@router.put("/notification-groups/{group_id}", response_model=Group)
async def replace_group(group_id: UUID, body: NotificationGroupSpec,
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    group = await write_group(session, principal, body, group_id=group_id)
    result = await group_view(session, group)
    await session.commit()
    return result


@router.patch("/notification-groups/{group_id}", response_model=Group)
async def patch_group(group_id: UUID, body: GroupPatch, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    group = await write_group(session, principal, body, group_id=group_id)
    result = await group_view(session, group)
    await session.commit()
    return result


@router.delete("/notification-groups/{group_id}", response_model=Group)
async def delete_group(group_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    group = await disable_group(session, principal, group_id)
    result = await group_view(session, group)
    await session.commit()
    return result


@router.get("/email-templates", response_model=Page[Template])
async def list_templates(cursor: str | None = None, limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    rows, next_cursor = await paginate(session, select(EmailTemplate).where(EmailTemplate.is_default.is_(True)),
        EmailTemplate, cursor, limit)
    return Page[Template](items=[Template.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get("/email-templates/{template_id}", response_model=Template)
async def read_template(template_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    return await get_template(session, template_id)


@router.post("/email-templates/{template_id}/preview", response_model=RenderedEmail)
async def preview_template(template_id: UUID, body: NotificationPayload,
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    template = await get_template(session, template_id)
    if body.event.type != template.event_type:
        raise DomainError("template_binding_invalid", 422)
    return render_email(template.event_type, body.model_dump(mode="json"), version=template.version)


@router.get("/email-deliveries", response_model=Page[Delivery])
async def list_deliveries(cursor: str | None = None, limit: int = Query(25, ge=1, le=100),
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    rows, next_cursor = await paginate(session, select(EmailDelivery).where(
        EmailDelivery.workspace_id == principal.workspace_id), EmailDelivery, cursor, limit)
    return Page[Delivery](items=[Delivery.model_validate(row) for row in rows], next_cursor=next_cursor)


@router.get("/email-deliveries/{delivery_id}", response_model=Delivery)
async def read_delivery(delivery_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    row = await session.scalar(select(EmailDelivery).where(EmailDelivery.id == delivery_id,
        EmailDelivery.workspace_id == principal.workspace_id))
    if row is None:
        raise DomainError("not_found", 404)
    return row


@router.get("/monitors/{monitor_id}/notification-routes", response_model=RouteView)
async def read_routes(monitor_id: UUID, principal: Principal = Depends(current_principal),
    session: AsyncSession = Depends(session_dependency)):
    return await routes.get_routes(session, principal, monitor_id)


@router.post("/monitors/{monitor_id}/notification-routes/preview", response_model=RoutePreviewJob, status_code=202)
async def request_route_preview(monitor_id: UUID, body: RoutePreviewRequest,
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    result = await routes.request_preview(session, principal, monitor_id, body)
    await session.commit()
    return result


@router.get("/monitors/{monitor_id}/notification-routes/preview/{job_id}", response_model=PreviewJobStatus)
async def route_preview_status(monitor_id: UUID, job_id: UUID,
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    principal, monitor, version = await routes._monitor(session, principal, monitor_id, write=True)
    job = await session.scalar(select(CollectionJob).where(CollectionJob.id == job_id,
        CollectionJob.workspace_id == principal.workspace_id, CollectionJob.kind == "preview"))
    if job is None:
        raise DomainError("not_found", 404)
    await routes._registered_draft(session, principal, monitor.id, version.id, job.draft_id)
    return PreviewJobStatus(id=job.id, status=job.status, error_code=job.error_code)


@router.post("/monitors/{monitor_id}/notification-routes/preview/{job_id}", response_model=RoutePreview)
async def finish_route_preview(monitor_id: UUID, job_id: UUID, body: RoutePreviewFinish,
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    result = await routes.finish_preview(session, principal, monitor_id, body.draft_id, body.revision, job_id)
    response = RoutePreview.model_validate(result)
    await session.commit()
    return response


@router.post("/monitors/{monitor_id}/notification-routes/confirm", response_model=RouteUpdate)
async def confirm_route_preview(monitor_id: UUID, body: RouteConfirmRequest,
    principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    result = await routes.confirm_routes(session, principal, monitor_id, body)
    await session.commit()
    return result
