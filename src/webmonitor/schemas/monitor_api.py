"""Public monitoring resources, excluding leases and object-store internals."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from webmonitor.schemas.notification_api import Page, Resource
from webmonitor.schemas.notifications import EventType


class EvidenceView(Resource):
    id: UUID
    kind: Literal["html", "screenshot", "response"]
    content_type: str
    size_bytes: int


class SnapshotView(Resource):
    id: UUID
    extracted_data: dict[str, JsonValue]
    coverage: dict[str, JsonValue]
    evidence_refs: list[EvidenceView]
    collected_at: datetime
    final_url: str


class MonitorView(Resource):
    id: UUID
    name: str
    url: str
    status: Literal["active", "paused", "deleted"]
    collection_mode: Literal["http", "browser"]
    next_run_at: datetime
    baseline_status: Literal["pending_first_success", "ready"]
    baseline_snapshot_id: UUID | None
    health_status: Literal["healthy", "failed"]
    created_by_user_id: UUID
    created_at: datetime
    current_version_id: UUID | None


class MonitorDetail(MonitorView):
    spec: dict[str, JsonValue]
    version: int
    baseline: SnapshotView | None


class RunView(Resource):
    id: UUID
    monitor_id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled", "discarded"]
    trigger: Literal["scheduled", "manual"]
    attempt_count: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    diff: dict[str, JsonValue] | None
    warnings: list[str]


class AttemptView(Resource):
    id: UUID
    attempt_number: int
    status: Literal["running", "succeeded", "failed", "cancelled", "discarded"]
    started_at: datetime
    finished_at: datetime | None
    retryable: bool
    error_code: str | None
    error_message: str | None


class RunDetail(RunView):
    attempts: list[AttemptView]
    snapshot: SnapshotView | None
    evidence: list[EvidenceView]


class EventView(Resource):
    id: UUID
    monitor_id: UUID
    run_id: UUID
    type: EventType
    detected_at: datetime
    created_at: datetime
    change: dict[str, JsonValue]
    payload: dict[str, JsonValue]
    acknowledged_at: datetime | None
