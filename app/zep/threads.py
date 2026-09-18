"""Threads do Zep = chats do Talk."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from zep_cloud.core.api_error import ApiError
from zep_cloud.types.message import Message

from app.talk.normalize import NormalizedMessage
from app.zep.client import get_zep

log = logging.getLogger(__name__)

# Limite documentado: 30 mensagens por chamada.
MESSAGES_PER_CALL = 30


async def ensure_thread(thread_id: str, user_id: str) -> bool:
    """Cria a thread se nao existe. Devolve True se criou."""
    try:
        await get_zep().thread.create(thread_id=thread_id, user_id=user_id)
        log.info("zep: thread criada %s (%s)", thread_id, user_id)
        return True
    except ApiError as exc:
        if exc.status_code == 409:
            return False
        raise


def to_zep_messages(items: Iterable[NormalizedMessage]) -> list[Message]:
    return [
        Message(
            role=m.role,
            name=m.name,
            content=m.content,
            created_at=m.created_at,
            metadata=m.metadata or None,
        )
        for m in items
    ]


def _chunks(seq: list, size: int) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


async def add_live_messages(
    thread_id: str, items: list[NormalizedMessage], *, strict_ontology: bool
) -> int:
    """Caminho ao vivo (webhook): ``thread.add_messages``."""
    msgs = to_zep_messages(items)
    for chunk in _chunks(msgs, MESSAGES_PER_CALL):
        await get_zep().thread.add_messages(
            thread_id, messages=chunk, strict_ontology=True if strict_ontology else None
        )
    return len(msgs)


async def add_history_messages(
    thread_id: str, items: list[NormalizedMessage], *, strict_ontology: bool
) -> int:
    """Caminho de backfill: ``thread.add_messages_batch`` (muito mais rapido)."""
    msgs = to_zep_messages(items)
    for chunk in _chunks(msgs, MESSAGES_PER_CALL):
        await get_zep().thread.add_messages_batch(
            thread_id, messages=chunk, strict_ontology=True if strict_ontology else None
        )
    return len(msgs)
