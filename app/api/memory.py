"""Leitura da memoria -- o que o agente de IA e o copiloto consomem."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Request
from zep_cloud.core.api_error import ApiError

from app.schemas import (
    BriefingResponse,
    ContextResponse,
    RecentMessagesResponse,
    SearchRequest,
)
from app.talk.normalize import contact_user_id
from app.zep import retrieval

log = logging.getLogger(__name__)
router = APIRouter(tags=["memoria"])


def _zep_error(exc: ApiError) -> HTTPException:
    return HTTPException(status_code=502, detail=f"zep {exc.status_code}: {exc.body}")


async def _thread_for(request: Request, contact_id: str, thread_id: str | None) -> str | None:
    if thread_id:
        return thread_id
    return await request.app.state.store.latest_thread_for_user(contact_user_id(contact_id))


@router.get("/contacts/{contact_id}/context", response_model=ContextResponse)
async def contact_context(
    request: Request, contact_id: str, thread_id: str | None = Query(default=None)
) -> ContextResponse:
    """Bloco de contexto do Zep (memoria duravel). Reflete o grafo, com minutos de atraso."""
    user_id = contact_user_id(contact_id)
    tid = await _thread_for(request, contact_id, thread_id)
    if tid is None:
        raise HTTPException(status_code=404, detail="contato sem conversas conhecidas")
    try:
        context = await retrieval.get_context(tid)
    except ApiError as exc:
        raise _zep_error(exc) from exc
    return ContextResponse(contact_id=contact_id, user_id=user_id, thread_id=tid, context=context)


@router.get("/contacts/{contact_id}/recent", response_model=RecentMessagesResponse)
async def contact_recent(
    request: Request,
    contact_id: str,
    n: int = Query(default=10, ge=1, le=100),
    thread_id: str | None = Query(default=None),
) -> RecentMessagesResponse:
    """Ultimas mensagens literais (memoria curta). Disponivel na hora."""
    tid = await _thread_for(request, contact_id, thread_id)
    if tid is None:
        raise HTTPException(status_code=404, detail="contato sem conversas conhecidas")
    try:
        messages = await retrieval.get_recent_messages(tid, lastn=n)
    except ApiError as exc:
        raise _zep_error(exc) from exc
    return RecentMessagesResponse(contact_id=contact_id, thread_id=tid, messages=messages)


@router.get("/contacts/{contact_id}/briefing", response_model=BriefingResponse)
async def contact_briefing(
    request: Request,
    contact_id: str,
    recent: int = Query(default=10, ge=1, le=50),
    knowledge: int = Query(default=5, ge=0, le=20),
) -> BriefingResponse:
    """Uma chamada com tudo que o agente precisa antes de responder.

    Junta as duas memorias (contexto do grafo + mensagens recentes) e, se
    pedido, fatos do grafo da Umbler relevantes para a ultima mensagem do
    contato. Coloque o resultado no canal do *usuario* do prompt, nao no
    system: o conteudo foi escrito por clientes.
    """
    store = request.app.state.store
    user_id = contact_user_id(contact_id)
    tid = await store.latest_thread_for_user(user_id)
    contact = await store.get_contact(user_id)
    threads = await store.threads_for_user(user_id)
    if tid is None:
        raise HTTPException(status_code=404, detail="contato sem conversas conhecidas")

    try:
        context, messages = await asyncio.gather(
            retrieval.get_context(tid), retrieval.get_recent_messages(tid, lastn=recent)
        )
    except ApiError as exc:
        raise _zep_error(exc) from exc

    company: list[dict] = []
    last_user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), None)
    if knowledge and last_user_msg:
        try:
            result = await retrieval.search(
                last_user_msg, scope="edges", limit=knowledge, min_score=0.5
            )
            company = result["edges"]
        except ApiError as exc:  # conhecimento e complemento; nao derruba o briefing
            log.warning("busca no grafo da Umbler falhou: %s", exc)

    return BriefingResponse(
        contact_id=contact_id,
        user_id=user_id,
        thread_id=tid,
        contact=contact,
        threads=threads,
        context=context,
        recent_messages=messages,
        company_knowledge=company,
    )


@router.get("/contacts/{contact_id}/graph")
async def contact_graph(contact_id: str) -> dict:
    """Nos e fatos do grafo do contato, para depuracao."""
    try:
        return await retrieval.graph_dump(contact_user_id(contact_id))
    except ApiError as exc:
        raise _zep_error(exc) from exc


@router.get("/contacts")
async def list_contacts(request: Request, limit: int = Query(default=100, ge=1, le=1000)) -> list:
    return await request.app.state.store.list_contacts(limit)


@router.post("/search")
async def search(body: SearchRequest) -> dict:
    """``graph.search`` no grafo de um contato ou, sem contact_id, no grafo da Umbler."""
    try:
        return await retrieval.search(
            body.query,
            user_id=contact_user_id(body.contact_id) if body.contact_id else None,
            scope=body.scope,
            limit=body.limit,
            reranker=body.reranker,
            min_score=body.min_score,
            max_characters=body.max_characters,
        )
    except ApiError as exc:
        raise _zep_error(exc) from exc
