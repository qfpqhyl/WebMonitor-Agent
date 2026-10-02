"""Evidence is authorized per workspace and never executed as same-origin HTML."""
from uuid import UUID
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from webmonitor.api.dependencies import AuthenticatedPrincipal, DatabaseSession
from webmonitor.api.errors import DomainError
from webmonitor.db.models.monitoring import Evidence
from webmonitor.storage.evidence import stream_evidence

router=APIRouter(prefix="/api/v1",tags=["evidence"])

async def response_for(evidence):
    is_image=evidence.kind=="screenshot"
    return StreamingResponse(await stream_evidence(evidence.bucket,evidence.object_key),media_type="image/png" if is_image else "text/plain; charset=utf-8",headers={"Content-Disposition":f"{'inline' if is_image else 'attachment'}; filename=\"{evidence.id}.{'png' if is_image else 'txt'}\"","X-Content-Type-Options":"nosniff","Cache-Control":"private, no-store","Content-Security-Policy":"default-src 'none'; sandbox"})

@router.get("/runs/{run_id}/evidence/{evidence_id}")
async def run_evidence(run_id:UUID,evidence_id:UUID,session:DatabaseSession,principal:AuthenticatedPrincipal):
    evidence=await session.scalar(select(Evidence).where(Evidence.id==evidence_id,Evidence.run_id==run_id,Evidence.workspace_id==principal.workspace_id))
    if evidence is None:
        raise DomainError("not_found",404)
    return await response_for(evidence)

@router.get("/previews/{preview_id}/evidence/{evidence_id}")
async def preview_evidence(preview_id:UUID,evidence_id:UUID,session:DatabaseSession,principal:AuthenticatedPrincipal):
    evidence=await session.scalar(select(Evidence).where(Evidence.id==evidence_id,Evidence.preview_id==preview_id,Evidence.workspace_id==principal.workspace_id))
    if evidence is None:
        raise DomainError("not_found",404)
    return await response_for(evidence)
