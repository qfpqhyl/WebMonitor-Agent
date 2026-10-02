"""Import every mapped class before inspecting or creating Base.metadata."""

from webmonitor.db.models.accounts import (
    AuditLog,
    Invitation,
    LoginRateLimit,
    Membership,
    SchemaMetadata,
    Session,
    User,
    WorkerHeartbeat,
    Workspace,
)
from webmonitor.db.models.conversations import (
    AgentCheckpoint,
    AgentRun,
    AgentSessionItem,
    Approval,
    Conversation,
    ConversationEvent,
    Draft,
    DraftRevision,
    IdempotencyRecord,
    Message,
    Preview,
)
from webmonitor.db.models.monitoring import (
    Attempt,
    CollectionJob,
    Event,
    Evidence,
    Monitor,
    MonitorVersion,
    Run,
    Snapshot,
)
from webmonitor.db.models.notifications import (
    EmailDelivery,
    EmailTemplate,
    MonitorNotificationRoute,
    NotificationGroup,
    NotificationGroupMember,
    Outbox,
)

__all__ = [
    "AgentCheckpoint", "AgentRun", "AgentSessionItem", "Approval", "Attempt",
    "AuditLog", "CollectionJob", "Conversation", "ConversationEvent", "Draft",
    "DraftRevision", "EmailDelivery", "EmailTemplate", "Event", "Evidence",
    "IdempotencyRecord", "Invitation", "LoginRateLimit", "Membership", "Message",
    "Monitor", "MonitorNotificationRoute", "MonitorVersion", "NotificationGroup",
    "NotificationGroupMember", "Outbox", "Preview", "Run", "SchemaMetadata",
    "Session", "Snapshot", "User", "WorkerHeartbeat", "Workspace",
]
