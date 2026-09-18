"""Modelos de request/response da API HTTP."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WebhookAck(BaseModel):
    accepted: bool
    duplicate: bool
    event_id: str


class ContextResponse(BaseModel):
    contact_id: str
    user_id: str
    thread_id: str | None
    context: str


class RecentMessagesResponse(BaseModel):
    contact_id: str
    thread_id: str | None
    messages: list[dict[str, Any]]


class BriefingResponse(BaseModel):
    """Tudo que um agente precisa antes de responder: memoria curta + duravel."""

    contact_id: str
    user_id: str
    thread_id: str | None
    contact: dict[str, Any] | None
    threads: list[dict[str, Any]]
    context: str
    recent_messages: list[dict[str, Any]]
    company_knowledge: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Fatos do grafo da Umbler relevantes para a ultima mensagem.",
    )


class SearchRequest(BaseModel):
    query: str
    contact_id: str | None = Field(
        None, description="Busca no grafo deste contato. Omitido = grafo da Umbler."
    )
    scope: Literal["edges", "nodes", "episodes", "auto"] = "edges"
    limit: int = Field(10, ge=1, le=50)
    reranker: Literal["cross_encoder", "rrf", "mmr", "episode_mentions"] | None = "cross_encoder"
    min_score: float | None = Field(None, description="Com cross_encoder, ~0.5 separa ruido.")
    max_characters: int | None = Field(None, description="Para scope=auto.")


class KnowledgeRequest(BaseModel):
    data: str | dict[str, Any] | list[Any]
    data_type: Literal["text", "json"] = "text"
    source_description: str | None = None
    created_at: str | None = Field(None, description="RFC3339 de quando o conteudo passou a valer.")
    metadata: dict[str, Any] | None = None


class KnowledgeBatchItem(BaseModel):
    text: str
    source_description: str | None = None
    created_at: str | None = None


class KnowledgeBatchRequest(BaseModel):
    items: list[KnowledgeBatchItem]


class EpisodesResponse(BaseModel):
    graph_id: str
    episodes: list[dict[str, Any]]


class OntologyResponse(BaseModel):
    matches: bool
    missing_entity_types: list[str]
    missing_edge_types: list[str]
    extra_entity_types: list[str]
    extra_edge_types: list[str]


class HealthResponse(BaseModel):
    status: str
    queue_size: int
    stats: dict[str, int]
