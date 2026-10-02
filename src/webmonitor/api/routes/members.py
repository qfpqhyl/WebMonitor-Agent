"""Workspace membership availability, restricted to administrators."""
from uuid import UUID
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from webmonitor.api.dependencies import current_principal
from webmonitor.api.errors import DomainError
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import AuditLog, Membership, Session, User
from webmonitor.db.session import session_dependency
from webmonitor.schemas.identity import Principal

router = APIRouter(prefix="/api/v1/members", tags=["members"])

class Availability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool

@router.get("")
async def list_members(principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    if principal.role != "admin":
        raise DomainError("forbidden", 403)
    rows = (await session.execute(select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.workspace_id == principal.workspace_id).order_by(User.email))).all()
    return {"items": [{"id": str(m.id), "user_id": str(u.id), "email": u.email, "display_name": u.display_name, "role": m.role, "active": m.active} for m, u in rows]}

@router.patch("/{membership_id}")
async def set_availability(membership_id: UUID, body: Availability, principal: Principal = Depends(current_principal), session: AsyncSession = Depends(session_dependency)):
    row = await session.scalar(select(Membership).where(Membership.id == membership_id, Membership.workspace_id == principal.workspace_id).with_for_update())
    if row is None:
        raise DomainError("not_found", 404)
    if principal.role != "admin":
        raise DomainError("forbidden", 403)
    row.active = body.active
    if not body.active:
        await session.execute(update(Session).where(Session.workspace_id == principal.workspace_id, Session.user_id == row.user_id, Session.revoked_at.is_(None)).values(revoked_at=utc_now()))
    session.add(AuditLog(workspace_id=principal.workspace_id, actor_user_id=principal.user_id, action="membership.availability", resource_type="membership", resource_id=row.id, details={"active": body.active}))
    await session.commit()
    return {"id": str(row.id), "active": row.active}
