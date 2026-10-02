"""Production and preview workers share a database-wide two-browser budget."""
import asyncio
import os
from uuid import uuid4
import pytest
from sqlalchemy import select,update
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from webmonitor.db.base import utc_now
from webmonitor.db.models.accounts import Workspace
from webmonitor.db.models.monitoring import CollectionJob
from webmonitor.workers.collection import claim_preview

@pytest.mark.asyncio
async def test_concurrent_browser_claims_stop_at_two():
    url=os.environ.get('TEST_DATABASE_URL')
    if not url:pytest.skip('Requires isolated initialized PostgreSQL')
    engine=create_async_engine(url)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    async with factory.begin() as session:
        workspace=Workspace(name='Browser capacity boundary',slug=uuid4().hex)
        session.add(workspace);await session.flush()
        for _ in range(3):
            session.add(CollectionJob(workspace_id=workspace.id,kind='analyze',collection_mode='browser',url='https://example.com/',status='queued'))
    async def claim(index):
        async with factory.begin() as session:
            job=await claim_preview(session,'browser',f'capacity-{index}')
            return job.id if job else None
    try:
        claimed=await asyncio.gather(*(claim(index) for index in range(3)))
        assert sum(identity is not None for identity in claimed)==2
        async with factory() as session:
            rows=(await session.scalars(select(CollectionJob).where(CollectionJob.workspace_id==workspace.id))).all()
            assert sorted(row.status for row in rows)==['queued','running','running']
    finally:
        async with factory.begin() as session:
            await session.execute(update(CollectionJob).where(CollectionJob.workspace_id==workspace.id).values(status='cancelled',lease_expires_at=None,finished_at=utc_now()))
        await engine.dispose()
