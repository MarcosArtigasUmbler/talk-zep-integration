"""Leitura: o que o agente e o copiloto consomem.

Duas memorias (APRENDIZADOS-ZEP.md, secao 4): a **curta** vem literal de
``thread.get`` e esta disponivel na hora; a **duravel** vem de
``thread.get_user_context`` / ``graph.search`` e leva minutos para refletir
uma mensagem nova. Um agente que responde no mesmo minuto precisa das duas.
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.zep.client import get_zep


async def get_context(thread_id: str, *, template_id: str | None = None) -> str:
    resp = await get_zep().thread.get_user_context(thread_id, template_id=template_id)
    return resp.context or ""


async def get_recent_messages(thread_id: str, *, lastn: int = 10) -> list[dict[str, Any]]:
    resp = await get_zep().thread.get(thread_id, lastn=lastn)
    return [
        {
            "role": m.role,
            "name": m.name,
            "content": m.content,
            "created_at": m.created_at,
            "metadata": m.metadata,
        }
        for m in (resp.messages or [])
    ]


async def search(
    query: str,
    *,
    user_id: str | None = None,
    graph_id: str | None = None,
    scope: str = "edges",
    limit: int = 10,
    reranker: str | None = "cross_encoder",
    min_score: float | None = None,
    max_characters: int | None = None,
) -> dict[str, Any]:
    """``graph.search`` num grafo de contato (user_id) ou no grafo da Umbler (graph_id)."""
    if not user_id and not graph_id:
        graph_id = get_settings().zep_org_graph_id
    resp = await get_zep().graph.search(
        query=query,
        user_id=user_id,
        graph_id=graph_id,
        scope=scope,
        limit=limit,
        reranker=reranker,
        max_characters=max_characters,
    )

    def keep(score: float | None) -> bool:
        return min_score is None or (score or 0.0) >= min_score

    edges = [
        {
            "fact": e.fact,
            "name": e.name,
            "score": e.score,
            "valid_at": e.valid_at,
            "invalid_at": e.invalid_at,
            "expired_at": e.expired_at,
        }
        for e in (resp.edges or [])
        if keep(e.score)
    ]
    nodes = [
        {"name": n.name, "labels": n.labels, "summary": n.summary, "score": n.score}
        for n in (resp.nodes or [])
        if keep(n.score)
    ]
    episodes = [
        {
            "content": ep.content,
            "source": ep.source,
            "created_at": ep.created_at,
            "score": ep.score,
        }
        for ep in (resp.episodes or [])
    ]
    return {
        "context": getattr(resp, "context", None),
        "edges": edges,
        "nodes": nodes,
        "episodes": episodes,
    }


async def graph_dump(user_id: str) -> dict[str, Any]:
    zep = get_zep()
    nodes = await zep.graph.node.get_by_user_id(user_id=user_id)
    edges = await zep.graph.edge.get_by_user_id(user_id=user_id)
    return {
        "nodes": [
            {"name": n.name, "labels": n.labels, "summary": n.summary} for n in (nodes or [])
        ],
        "edges": [
            {
                "name": e.name,
                "fact": e.fact,
                "valid_at": e.valid_at,
                "invalid_at": e.invalid_at,
                "expired_at": e.expired_at,
            }
            for e in (edges or [])
        ],
    }
