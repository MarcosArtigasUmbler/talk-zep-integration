"""Conhecimento geral da Umbler -> grafo avulso (``graph_id``).

Tudo que entra aqui e legivel pelo agente de qualquer contato. **Nunca dado de
cliente.** Limite do Zep: 10.000 caracteres por episodio, por isso textos
maiores sao quebrados em paragrafos.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from zep_cloud.core.api_error import ApiError
from zep_cloud.types.episode import Episode
from zep_cloud.types.episode_data import EpisodeData

from app.config import get_settings
from app.zep.client import get_zep

log = logging.getLogger(__name__)

DataType = Literal["text", "json"]
MAX_EPISODE_CHARS = 10_000
TARGET_CHUNK_CHARS = 6_000
BATCH_SIZE = 20


def chunk_text(
    text: str, target: int = TARGET_CHUNK_CHARS, hard_max: int = MAX_EPISODE_CHARS
) -> list[str]:
    """Quebra por paragrafo, sem cortar no meio de um fato."""
    text = text.strip()
    if len(text) <= hard_max:
        return [text] if text else []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        while len(para) > hard_max:  # paragrafo gigante: corta em frases
            cut = para.rfind(". ", 0, hard_max)
            cut = cut + 1 if cut > 0 else hard_max
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if size + len(para) + 2 > target and current:
            chunks.append("\n\n".join(current))
            current, size = [], 0
        if para:
            current.append(para)
            size += len(para) + 2
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def serialize(data: str | dict | list, data_type: DataType) -> str:
    if data_type == "json":
        if isinstance(data, str):
            return data
        return json.dumps(data, ensure_ascii=False, sort_keys=True)
    if not isinstance(data, str):
        raise TypeError("data precisa ser str para type=text")
    return data


async def ensure_org_graph(graph_id: str | None = None) -> bool:
    """Cria o grafo da Umbler se nao existir. Devolve True se criou."""
    gid = graph_id or get_settings().zep_org_graph_id
    try:
        await get_zep().graph.create(
            graph_id=gid,
            name="Umbler — conhecimento da empresa",
            description=(
                "Produtos, planos, politica comercial, integracoes, seguranca e respostas "
                "padrao da Umbler. Compartilhado por toda a operacao; nunca dado de cliente."
            ),
        )
        return True
    except ApiError as exc:
        if exc.status_code in (400, 409):
            return False
        raise


def _describe(source: str | None, index: int, total: int) -> str | None:
    if source and total > 1:
        return f"{source} (parte {index + 1}/{total})"
    return source


async def add_knowledge(
    data: str | dict | list,
    *,
    data_type: DataType = "text",
    source_description: str | None = None,
    created_at: str | None = None,
    metadata: dict[str, Any] | None = None,
    graph_id: str | None = None,
) -> list[Episode]:
    gid = graph_id or get_settings().zep_org_graph_id
    payload = serialize(data, data_type)
    if data_type == "json":
        if len(payload) > MAX_EPISODE_CHARS:
            raise ValueError(f"json com {len(payload)} caracteres; o limite e {MAX_EPISODE_CHARS}")
        pieces = [payload]
    else:
        pieces = chunk_text(payload)
    if not pieces:
        raise ValueError("conteudo vazio")

    episodes: list[Episode] = []
    if len(pieces) == 1:
        ep = await get_zep().graph.add(
            graph_id=gid,
            type=data_type,
            data=pieces[0],
            source_description=source_description,
            created_at=created_at,
            metadata=metadata,
        )
        episodes.append(ep)
    else:
        batch = [
            EpisodeData(
                data=p,
                type=data_type,
                source_description=_describe(source_description, i, len(pieces)),
                created_at=created_at,
                metadata=metadata,
            )
            for i, p in enumerate(pieces)
        ]
        episodes.extend(await get_zep().graph.add_batch(graph_id=gid, episodes=batch) or [])
    log.info("zep: %d episodio(s) no grafo %s", len(episodes), gid)
    return episodes


async def add_knowledge_batch(
    items: list[tuple[str, str | None, str | None]], *, graph_id: str | None = None
) -> list[Episode]:
    """items: (texto, source_description, created_at). Carga inicial em lote."""
    gid = graph_id or get_settings().zep_org_graph_id
    batch: list[EpisodeData] = []
    for text, source, created_at in items:
        pieces = chunk_text(text)
        for i, p in enumerate(pieces):
            batch.append(
                EpisodeData(
                    data=p,
                    type="text",
                    source_description=_describe(source, i, len(pieces)),
                    created_at=created_at,
                )
            )
    out: list[Episode] = []
    for i in range(0, len(batch), BATCH_SIZE):
        out.extend(
            await get_zep().graph.add_batch(graph_id=gid, episodes=batch[i : i + BATCH_SIZE]) or []
        )
    return out


async def add_contact_note(
    user_id: str, text: str, *, source_description: str, created_at: str | None
) -> Episode:
    """Nota interna do atendente -> episodio de texto no grafo DO CONTATO."""
    return await get_zep().graph.add(
        user_id=user_id,
        type="text",
        data=text[:MAX_EPISODE_CHARS],
        source_description=source_description,
        created_at=created_at,
    )
