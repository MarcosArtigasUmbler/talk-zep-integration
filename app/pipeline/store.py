"""Persistencia local (SQLite) da integracao.

Guarda o que o Zep nao guarda por nos:

* ``events``   -- diario dos webhooks recebidos. E a idempotencia (o Talk
  reenvia ate 2 vezes com o mesmo EventId) e a fila duravel: o que ficou
  pendente numa queda e reprocessado na subida.
* ``messages`` -- ids de mensagem ja enviados ao Zep (o mesmo LastMessage pode
  chegar em eventos diferentes).
* ``facts``    -- fatos estruturados ja gravados, para nao duplicar fact_triple.
* ``contacts`` -- ultimo estado da ficha do contato enviado ao Zep.
* ``chats``    -- chat -> usuario/thread, para o copiloto achar a thread atual.
* ``members``  -- cache de nomes de atendentes (o webhook traz so o id).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT PRIMARY KEY,
    type        TEXT,
    event_date  TEXT,
    received_at TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    summary     TEXT,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    event_id   TEXT,
    thread_id  TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS facts (
    fact_key   TEXT PRIMARY KEY,
    user_id    TEXT,
    fact_name  TEXT,
    fact       TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    user_id     TEXT PRIMARY KEY,
    contact_id  TEXT,
    name        TEXT,
    phone       TEXT,
    fingerprint TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS chats (
    chat_id       TEXT PRIMARY KEY,
    user_id       TEXT,
    thread_id     TEXT,
    created_at    TEXT,
    last_event_at TEXT,
    open          INTEGER,
    sector        TEXT,
    member_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_chats_user ON chats(user_id, last_event_at);
CREATE TABLE IF NOT EXISTS members (
    member_id  TEXT PRIMARY KEY,
    name       TEXT,
    updated_at TEXT
);
"""

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_RETRY = "retry"
STATUS_FAILED = "failed"

RESUMABLE_STATUSES = (STATUS_PENDING, STATUS_PROCESSING, STATUS_RETRY)


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def fact_key(user_id: str, fact_name: str, target: str, fact: str) -> str:
    raw = f"{user_id}|{fact_name}|{target.lower()}|{fact.lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Store nao foi aberto"
        return self._db

    # --- eventos -------------------------------------------------------------

    async def record_event(
        self, event_id: str, event_type: str | None, event_date: str | None, payload: Any
    ) -> bool:
        """Grava o evento. Devolve False se o EventId ja era conhecido."""
        cur = await self.db.execute(
            "INSERT OR IGNORE INTO events(event_id, type, event_date, received_at, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_id, event_type, event_date, now_iso(), json.dumps(payload, ensure_ascii=False)),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def get_event(self, event_id: str) -> dict | None:
        cur = await self.db.execute("SELECT * FROM events WHERE event_id = ?", (event_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def mark(
        self,
        event_id: str,
        status: str,
        *,
        error: str | None = None,
        summary: str | None = None,
        bump_attempts: bool = False,
    ) -> None:
        await self.db.execute(
            "UPDATE events SET status = ?, last_error = COALESCE(?, last_error), "
            "summary = COALESCE(?, summary), attempts = attempts + ? WHERE event_id = ?",
            (status, error, summary, 1 if bump_attempts else 0, event_id),
        )
        await self.db.commit()

    async def reset_event(self, event_id: str) -> bool:
        cur = await self.db.execute(
            "UPDATE events SET status = 'pending', attempts = 0, last_error = NULL "
            "WHERE event_id = ?",
            (event_id,),
        )
        await self.db.commit()
        return cur.rowcount == 1

    async def pending_event_ids(self) -> list[str]:
        placeholders = ",".join("?" for _ in RESUMABLE_STATUSES)
        cur = await self.db.execute(
            f"SELECT event_id FROM events WHERE status IN ({placeholders}) ORDER BY received_at",
            RESUMABLE_STATUSES,
        )
        return [row["event_id"] for row in await cur.fetchall()]

    async def list_events(self, status: str | None = None, limit: int = 50) -> list[dict]:
        cols = "event_id, type, event_date, received_at, status, attempts, last_error, summary"
        if status:
            cur = await self.db.execute(
                f"SELECT {cols} FROM events WHERE status = ? ORDER BY received_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cur = await self.db.execute(
                f"SELECT {cols} FROM events ORDER BY received_at DESC LIMIT ?", (limit,)
            )
        return [dict(r) for r in await cur.fetchall()]

    async def stats(self) -> dict[str, int]:
        cur = await self.db.execute("SELECT status, COUNT(*) AS n FROM events GROUP BY status")
        out = {f"events_{row['status']}": row["n"] for row in await cur.fetchall()}
        for table in ("messages", "facts", "contacts", "chats"):
            cur = await self.db.execute(f"SELECT COUNT(*) AS n FROM {table}")
            out[table] = (await cur.fetchone())["n"]
        return out

    # --- mensagens -----------------------------------------------------------

    async def message_seen(self, message_id: str) -> bool:
        cur = await self.db.execute("SELECT 1 FROM messages WHERE message_id = ?", (message_id,))
        return await cur.fetchone() is not None

    async def mark_messages(self, items: list[tuple[str, str | None, str]]) -> None:
        """items: (message_id, event_id, thread_id)."""
        await self.db.executemany(
            "INSERT OR IGNORE INTO messages(message_id, event_id, thread_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            [(m, e, t, now_iso()) for m, e, t in items],
        )
        await self.db.commit()

    # --- fatos ---------------------------------------------------------------

    async def fact_seen(self, key: str) -> bool:
        cur = await self.db.execute("SELECT 1 FROM facts WHERE fact_key = ?", (key,))
        return await cur.fetchone() is not None

    async def mark_fact(self, key: str, user_id: str, fact_name: str, fact: str) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO facts(fact_key, user_id, fact_name, fact, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, user_id, fact_name, fact, now_iso()),
        )
        await self.db.commit()

    # --- contatos ------------------------------------------------------------

    async def contact_fingerprint(self, user_id: str) -> str | None:
        cur = await self.db.execute(
            "SELECT fingerprint FROM contacts WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return row["fingerprint"] if row else None

    async def upsert_contact(
        self, user_id: str, contact_id: str, name: str | None, phone: str | None, fingerprint: str
    ) -> None:
        await self.db.execute(
            "INSERT INTO contacts(user_id, contact_id, name, phone, fingerprint, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "name = excluded.name, phone = excluded.phone, fingerprint = excluded.fingerprint, "
            "updated_at = excluded.updated_at",
            (user_id, contact_id, name, phone, fingerprint, now_iso()),
        )
        await self.db.commit()

    async def get_contact(self, user_id: str) -> dict | None:
        cur = await self.db.execute("SELECT * FROM contacts WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_contacts(self, limit: int = 100) -> list[dict]:
        cur = await self.db.execute(
            "SELECT * FROM contacts ORDER BY updated_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # --- chats ---------------------------------------------------------------

    async def chat_known(self, chat_id: str) -> bool:
        cur = await self.db.execute("SELECT 1 FROM chats WHERE chat_id = ?", (chat_id,))
        return await cur.fetchone() is not None

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
        await self.db.execute(
            "INSERT INTO chats(chat_id, user_id, thread_id, created_at, last_event_at, open, "
            "sector, member_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET "
            "last_event_at = COALESCE(excluded.last_event_at, chats.last_event_at), "
            "open = COALESCE(excluded.open, chats.open), "
            "sector = COALESCE(excluded.sector, chats.sector), "
            "member_id = COALESCE(excluded.member_id, chats.member_id)",
            (
                chat_id,
                user_id,
                thread_id,
                created_at,
                last_event_at or now_iso(),
                None if open_ is None else int(open_),
                sector,
                member_id,
            ),
        )
        await self.db.commit()

    async def latest_thread_for_user(self, user_id: str) -> str | None:
        cur = await self.db.execute(
            "SELECT thread_id FROM chats WHERE user_id = ? ORDER BY last_event_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return row["thread_id"] if row else None

    async def threads_for_user(self, user_id: str) -> list[dict]:
        cur = await self.db.execute(
            "SELECT chat_id, thread_id, created_at, last_event_at, open, sector, member_id "
            "FROM chats WHERE user_id = ? ORDER BY last_event_at DESC",
            (user_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    # --- membros -------------------------------------------------------------

    async def member_name(self, member_id: str) -> str | None:
        cur = await self.db.execute("SELECT name FROM members WHERE member_id = ?", (member_id,))
        row = await cur.fetchone()
        return row["name"] if row else None

    async def set_member_names(self, names: dict[str, str]) -> None:
        if not names:
            return
        await self.db.executemany(
            "INSERT INTO members(member_id, name, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(member_id) DO UPDATE SET name = excluded.name, "
            "updated_at = excluded.updated_at",
            [(k, v, now_iso()) for k, v in names.items()],
        )
        await self.db.commit()

    async def all_member_names(self) -> dict[str, str]:
        cur = await self.db.execute("SELECT member_id, name FROM members")
        return {r["member_id"]: r["name"] for r in await cur.fetchall()}
