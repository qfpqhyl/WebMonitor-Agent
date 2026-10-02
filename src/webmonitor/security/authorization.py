"""Uniform resource hiding and role/ownership checks for domain services."""
from typing import Protocol, TypeVar
from uuid import UUID

from webmonitor.api.errors import DomainError
from webmonitor.schemas.identity import Principal


class WorkspaceResource(Protocol):
    workspace_id: UUID


class OwnedResource(WorkspaceResource, Protocol):
    created_by_user_id: UUID


Resource = TypeVar("Resource", bound=WorkspaceResource)
Owned = TypeVar("Owned", bound=OwnedResource)


def require_workspace(principal: Principal, resource: Resource | None) -> Resource:
    """Hide nonexistent and foreign-workspace resources identically."""
    if resource is None or resource.workspace_id != principal.workspace_id:
        raise DomainError("not_found", status=404)
    return resource


def require_admin(principal: Principal) -> None:
    if principal.role != "admin":
        raise DomainError("forbidden", status=403)


def require_admin_write(principal: Principal, resource: Resource | None) -> Resource:
    resource = require_workspace(principal, resource)
    require_admin(principal)
    return resource


def require_owner_or_admin(principal: Principal, resource: Owned | None) -> Owned:
    """Authorize owner-scoped actions, e.g. pause/resume a monitor."""
    resource = require_workspace(principal, resource)
    if principal.role != "admin" and resource.created_by_user_id != principal.user_id:
        raise DomainError("forbidden", status=403)
    return resource
