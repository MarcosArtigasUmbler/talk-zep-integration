"""Conhecimento geral da Umbler -> grafo avulso."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from zep_cloud.core.api_error import ApiError

from app.schemas import EpisodesResponse, KnowledgeBatchRequest, KnowledgeRequest
from app.zep import knowledge

router = APIRouter(prefix="/knowledge", tags=["conhecimento"])


def _episodes(request: Request, episodes: list) -> EpisodesResponse:
    return EpisodesResponse(
        graph_id=request.app.state.settings.zep_org_graph_id,
        episodes=[{"uuid": e.uuid_, "processed": e.processed} for e in episodes],
    )


@router.post("", response_model=EpisodesResponse, status_code=202)
async def add_knowledge(request: Request, body: KnowledgeRequest) -> EpisodesResponse:
    """Um texto ou JSON de conhecimento da empresa. Textos longos sao divididos."""
    try:
        episodes = await knowledge.add_knowledge(
            body.data,
            data_type=body.data_type,
            source_description=body.source_description,
            created_at=body.created_at,
            metadata=body.metadata,
        )
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=f"zep {exc.status_code}: {exc.body}") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _episodes(request, episodes)


@router.post("/batch", response_model=EpisodesResponse, status_code=202)
async def add_knowledge_batch(request: Request, body: KnowledgeBatchRequest) -> EpisodesResponse:
    """Carga inicial: varios textos numa chamada (``graph.add_batch``)."""
    try:
        episodes = await knowledge.add_knowledge_batch(
            [(i.text, i.source_description, i.created_at) for i in body.items]
        )
    except ApiError as exc:
        raise HTTPException(status_code=502, detail=f"zep {exc.status_code}: {exc.body}") from exc
    return _episodes(request, episodes)
