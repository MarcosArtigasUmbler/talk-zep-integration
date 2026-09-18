"""Saude, fila e ontologia."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from zep_cloud.core.api_error import ApiError

from app.schemas import HealthResponse, OntologyResponse
from app.zep.ontology import verify_ontology

router = APIRouter(tags=["admin"])

# /health fica fora da chave de API: o HEALTHCHECK do container e o balanceador
# precisam chamar sem credencial. Ele nao devolve dado de cliente.
health_router = APIRouter(tags=["admin"])


@health_router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    worker = getattr(request.app.state, "worker", None)
    return HealthResponse(
        status="ok",
        queue_size=worker.size if worker else 0,
        stats=await request.app.state.store.stats(),
    )


@router.get("/ontology", response_model=OntologyResponse)
async def ontology() -> OntologyResponse:
    try:
        diff = await verify_ontology()
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=f"zep {exc.status_code}: {exc.body}") from exc
    return OntologyResponse(
        matches=diff.matches,
        missing_entity_types=list(diff.missing_entity_types),
        missing_edge_types=list(diff.missing_edge_types),
        extra_entity_types=list(diff.extra_entity_types),
        extra_edge_types=list(diff.extra_edge_types),
    )


@router.get("/admin/events")
async def list_events(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    return await request.app.state.store.list_events(status, limit)


@router.get("/admin/events/{event_id}")
async def get_event(request: Request, event_id: str) -> dict:
    row = await request.app.state.store.get_event(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="evento desconhecido")
    return row


@router.post("/admin/events/{event_id}/retry")
async def retry_event(request: Request, event_id: str) -> dict:
    """Recoloca um evento (falho ou nao) na fila."""
    store = request.app.state.store
    if not await store.reset_event(event_id):
        raise HTTPException(status_code=404, detail="evento desconhecido")
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.enqueue(event_id)
    return {"event_id": event_id, "queued": worker is not None}
