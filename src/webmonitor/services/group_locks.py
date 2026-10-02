"""Advisory locks synchronize recipient snapshots without granting collectors UPDATE."""
import hashlib
from sqlalchemy import text

async def lock_notification_groups(session,group_ids,*,exclusive=False):
    function="pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
    for group_id in sorted(set(group_ids),key=str):
        key=int.from_bytes(hashlib.sha256(f"notification-group:{group_id}".encode()).digest()[:8],"big",signed=True)
        await session.execute(text(f"SELECT {function}(:key)"),{"key":key})
