"""Contrato da persistencia local da integracao.

Guarda o que o Zep nao guarda por nos:

* ``events``   -- diario dos webhooks recebidos. E a idempotencia (o Talk
  reenvia ate 2 vezes com o mesmo EventId) e a fila duravel: o que ficou
  pendente numa queda e reprocessado na subida.
* ``messages`` -- ids de mensagem ja enviados ao Zep (o mesmo LastMessage pode
  chegar em eventos diferentes).
* ``facts``    -- fatos estruturados ja gravados, para nao duplicar fact_triple.
* ``contacts`` -- ultimo estado da ficha do contato (nome, telefone, tags).
* ``chats``    -- chat -> usuario/thread, para o copiloto achar a thread atual.
* ``members``  -- cache de nomes de atendentes (o webhook traz so o id).

Duas implementacoes: ``MongoStore`` (producao) e ``SQLiteStore`` (desenvolvimento
e testes). Ambas devolvem dicts com os mesmos campos.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import Any

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_RETRY = "retry"
STATUS_FAILED = "failed"

RESUMABLE_STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_RETRY)

EVENT_SUMMARY_FIELDS = (
    "event_id",
    "type",
    "event_date",
    "received_at",
    "status",
    "attempts",
    "last_error",
    "summary",
)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def fact_key(user_id: str, fact_name: str, target: str, fact: str) -> str:
    raw = f"{user_id}|{fact_name}|{target.lower()}|{fact.lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class Store(ABC):
    """Interface comum. Todos os metodos sao idempotentes onde faz sentido."""

    @abstractmethod
    async def open(self) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...

    # --- eventos -------------------------------------------------------------

    @abstractmethod
    async def record_event(
        self, event_id: str, event_type: str | None, event_date: str | None, payload: Any
    ) -> bool:
        """Grava o evento. Devolve False se o EventId ja era conhecido."""

    @abstractmethod
    async def get_event(self, event_id: str) -> dict | None: ...

    @abstractmethod
    async def mark(
        self,
        event_id: str,
        status: str,
        *,
        error: str | None = None,
        summary: str | None = None,
        bump_attempts: bool = False,
    ) -> None: ...

    @abstractmethod
    async def reset_event(self, event_id: str) -> bool: ...

    @abstractmethod
    async def pending_event_ids(self) -> list[str]: ...

    @abstractmethod
    async def list_events(self, status: str | None = None, limit: int = 50) -> list[dict]: ...

    @abstractmethod
    async def stats(self) -> dict[str, int]: ...

    # --- mensagens -----------------------------------------------------------

    @abstractmethod
    async def message_seen(self, message_id: str) -> bool: ...

    @abstractmethod
    async def mark_messages(self, items: list[tuple[str, str | None, str]]) -> None:
        """items: (message_id, event_id, thread_id)."""

    # --- fatos ---------------------------------------------------------------

    @abstractmethod
    async def fact_seen(self, key: str) -> bool: ...

    @abstractmethod
    async def mark_fact(self, key: str, user_id: str, fact_name: str, fact: str) -> None: ...

    # --- contatos ------------------------------------------------------------

    @abstractmethod
    async def contact_fingerprint(self, user_id: str) -> str | None: ...

    @abstractmethod
    async def upsert_contact(
        self,
        user_id: str,
        contact_id: str,
        name: str | None,
        phone: str | None,
        fingerprint: str,
        tags: list[str] | None = None,
    ) -> None: ...

    @abstractmethod
    async def get_contact(self, user_id: str) -> dict | None: ...

    @abstractmethod
    async def list_contacts(self, limit: int = 100) -> list[dict]: ...

    # --- chats ---------------------------------------------------------------

    @abstractmethod
    async def chat_known(self, chat_id: str) -> bool: ...

    @abstractmethod
    async def upsert_chat(
        self,
        chat_id: str,
        user_id: str,
        thread_id: str,
        *,
        created_at: str | None,
        last_event_at: str | None,
        open_: bool | None,
        sector: str | None,
        member_id: str | None,
    ) -> None:
        """Campos None nao sobrescrevem o valor ja guardado."""

    @abstractmethod
    async def latest_thread_for_user(self, user_id: str) -> str | None: ...

    @abstractmethod
    async def threads_for_user(self, user_id: str) -> list[dict]: ...

    # --- membros -------------------------------------------------------------

    @abstractmethod
    async def member_name(self, member_id: str) -> str | None: ...

    @abstractmethod
    async def set_member_names(self, names: dict[str, str]) -> None: ...

    @abstractmethod
    async def all_member_names(self) -> dict[str, str]: ...
