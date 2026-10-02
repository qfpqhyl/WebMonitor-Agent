"""Stable descending created_at/id keyset cursors, with strict malformed-input rejection."""
import base64
import binascii
import json
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import and_, or_
from webmonitor.api.errors import DomainError


def encode_cursor(row) -> str:
    value=json.dumps([row.created_at.astimezone(timezone.utc).isoformat(),str(row.id)],separators=(",",":"))
    return base64.urlsafe_b64encode(value.encode()).rstrip(b"=").decode()


def decode_cursor(cursor:str):
    try:
        if not cursor or len(cursor)>512:
            raise ValueError()
        data=json.loads(base64.b64decode(cursor+"="*(-len(cursor)%4),altchars=b"-_",validate=True))
        if not isinstance(data,list) or len(data)!=2 or not all(isinstance(x,str) for x in data):
            raise ValueError()
        timestamp=datetime.fromisoformat(data[0])
        if timestamp.tzinfo is None:
            raise ValueError()
        return timestamp,UUID(data[1])
    except (ValueError,TypeError,binascii.Error,UnicodeError):
        raise DomainError("cursor_invalid",422) from None


async def paginate(session,query,model,cursor=None,limit=25):
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100:
        raise DomainError("validation_failed",422)
    if cursor is not None:
        timestamp,identity=decode_cursor(cursor)
        query=query.where(or_(model.created_at<timestamp,and_(model.created_at==timestamp,model.id<identity)))
    rows=list((await session.scalars(query.order_by(model.created_at.desc(),model.id.desc()).limit(limit+1))).all())
    return rows[:limit],encode_cursor(rows[limit-1]) if len(rows)>limit else None
