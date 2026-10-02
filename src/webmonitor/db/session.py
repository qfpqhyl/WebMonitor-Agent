"""Process-local async database engine and transaction boundaries."""
from functools import lru_cache
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from webmonitor.config import get_settings

@lru_cache
def get_engine():
    return create_async_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5, max_overflow=5)

@lru_cache
def get_session_factory():
    return async_sessionmaker(get_engine(), expire_on_commit=False)

async def session_dependency():
    async with get_session_factory()() as session:
        yield session
