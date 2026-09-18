"""Backfill do historico do Talk para o Zep -- SOMENTE LEITURA na API do Talk.

Este script faz apenas requisicoes GET:

    GET /v1/chats/                          lista de chats (paginada)
    GET /v1/chats/{chatId}/relative-messages/  mensagens de um chat

Nada e criado, alterado ou apagado no Talk. Ele existe porque o webhook so
entrega o que acontece a partir do cadastro; o historico anterior precisa
entrar em lote (``thread.add_messages_batch``), em ordem cronologica, com as
datas reais -- e o Zep leva minutos por contato para processar.

Requer no .env: TALK_API_TOKEN e TALK_ORGANIZATION_ID.

Uso:
    python -m scripts.backfill_talk --days 90 --state Closed --dry-run
    python -m scripts.backfill_talk --days 90 --state All
    python -m scripts.backfill_talk --chat <chatId>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from dotenv import load_dotenv

load_dotenv()

from zep_cloud.core.api_error import ApiError

from app.config import get_settings
from app.pipeline.store import Store, create_store
from app.talk.members import MemberDirectory
from app.talk.models import Chat, Message, lower_keys, to_rfc3339
from app.talk.normalize import (
    chat_thread_id,
    contact_user_id,
    normalize_message,
    should_ingest_chat,
)
from app.zep.client import close_zep
from app.zep.knowledge import add_contact_note
from app.zep.threads import add_history_messages, ensure_thread
from app.zep.users import contact_fingerprint, ensure_user

PAGE = 50
MESSAGES_PAGE = 100


class TalkReader:
    """Leitor da API do Talk. Deliberadamente so tem GET."""

    def __init__(self) -> None:
        s = get_settings()
        if not (s.talk_api_token and s.talk_organization_id):
            raise SystemExit("defina TALK_API_TOKEN e TALK_ORGANIZATION_ID no .env")
        self.org = s.talk_organization_id
        self.client = httpx.AsyncClient(
            base_url=s.talk_api_base.rstrip("/"),
            headers={"Authorization": f"Bearer {s.talk_api_token}"},
            timeout=30,
        )

    async def get(self, path: str, **params) -> dict | list:
        params = {"organizationId": self.org, **{k: v for k, v in params.items() if v is not None}}
        for attempt in range(4):
            resp = await self.client.get(path, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return {}

    async def chats(self, state: str, since: datetime | None):
        skip = 0
        while True:
            page = await self.get(
                "/v1/chats/",
                ChatState=state,
                Skip=skip,
                Take=PAGE,
                DateStartCreatedAtUTC=since.isoformat().replace("+00:00", "Z") if since else None,
            )
            items = page.get("items") if isinstance(page, dict) else page
            if not items:
                return
            for raw in items:
                yield Chat.model_validate(lower_keys(raw))
            if len(items) < PAGE:
                return
            skip += PAGE

    async def chat(self, chat_id: str) -> Chat:
        raw = await self.get(f"/v1/chats/{chat_id}/")
        return Chat.model_validate(lower_keys(raw))

    async def messages(self, chat_id: str) -> list[Message]:
        """Anda para tras a partir de agora ate esgotar; devolve em ordem cronologica."""
        out: list[Message] = []
        cursor = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        while True:
            data = await self.get(
                f"/v1/chats/{chat_id}/relative-messages/",
                FromEventUTC=cursor,
                Take=MESSAGES_PAGE,
                Direction="TakeBefore",
                IncludeMetadata=False,
            )
            batch = [Message.model_validate(lower_keys(m)) for m in (data.get("messages") or [])]
            if not batch:
                break
            out.extend(batch)
            oldest = min((m.happened_at for m in batch if m.happened_at), default=None)
            if len(batch) < MESSAGES_PAGE or not oldest or oldest == cursor:
                break
            cursor = oldest
        seen: set[str] = set()
        unique = []
        for m in out:
            if m.id and m.id not in seen:
                seen.add(m.id)
                unique.append(m)
        unique.sort(key=lambda m: m.happened_at or "")
        return unique

    async def close(self) -> None:
        await self.client.aclose()


async def backfill_chat(
    reader: TalkReader, store: Store, members: MemberDirectory, chat: Chat, dry_run: bool
) -> str:
    settings = get_settings()
    ok, reason = should_ingest_chat(chat, ingest_groups=settings.ingest_group_chats)
    if not ok or chat.contact is None:
        return f"pulado: {reason}"
    contact = chat.contact
    user_id = contact_user_id(contact.id or "")
    thread_id = chat_thread_id(chat.id or "")

    messages = await reader.messages(chat.id or "")
    normalized = []
    notes = []
    for msg in messages:
        if msg.id and await store.message_seen(msg.id):
            continue
        member_ref = msg.sent_by_organization_member or chat.organization_member
        member_name = await members.name_for(member_ref.id if member_ref else None)
        n = normalize_message(msg, chat, member_name=member_name, event_date=None)
        if n is None:
            continue
        (notes if n.is_private else normalized).append(n)

    if dry_run:
        return f"{len(messages)} msgs lidas, {len(normalized)} a enviar, {len(notes)} notas"

    fingerprint = contact_fingerprint(contact)
    previous = await store.contact_fingerprint(user_id)
    if previous != fingerprint:
        await ensure_user(user_id, contact, update=previous is not None)
        await store.upsert_contact(
            user_id, contact.id or "", contact.name, contact.phone_number, fingerprint
        )
    await ensure_thread(thread_id, user_id)
    await store.upsert_chat(
        chat.id or "",
        user_id,
        thread_id,
        created_at=to_rfc3339(chat.created_at_utc),
        last_event_at=to_rfc3339(chat.event_at_utc),
        open_=chat.open,
        sector=chat.sector.name if chat.sector else None,
        member_id=chat.organization_member.id if chat.organization_member else None,
    )
    if normalized:
        await add_history_messages(
            thread_id, normalized, strict_ontology=settings.zep_strict_ontology
        )
        await store.mark_messages([(n.message_id, "backfill", thread_id) for n in normalized])
    if settings.ingest_private_notes:
        for n in notes:
            await add_contact_note(
                user_id,
                f"Nota interna de {n.name} no Umbler Talk: {n.content}",
                source_description="Nota interna do atendente no Umbler Talk",
                created_at=n.created_at,
            )
            await store.mark_messages([(n.message_id, "backfill", thread_id)])
    return f"{len(normalized)} mensagens + {len(notes)} notas enviadas"


async def main(days: int, state: str, chat_id: str | None, dry_run: bool, limit: int) -> int:
    settings = get_settings()
    reader = TalkReader()
    store = create_store(settings)
    await store.open()
    members = MemberDirectory(store, settings)
    await members.load()
    await members.refresh_from_api(force=True)
    done = 0
    try:
        if chat_id:
            chats = [await reader.chat(chat_id)]
        else:
            since = datetime.now(UTC) - timedelta(days=days) if days else None
            chats = [c async for c in reader.chats(state, since)]
        print(f"{len(chats)} chat(s) encontrados{' (dry-run)' if dry_run else ''}")
        for chat in chats:
            if limit and done >= limit:
                break
            label = f"{(chat.contact.name if chat.contact else '?') or '?':<30} chat {chat.id}"
            try:
                result = await backfill_chat(reader, store, members, chat, dry_run)
                print(f"  {label}: {result}")
                done += 1
            except ApiError as exc:
                print(f"  {label}: erro do Zep {exc.status_code}: {exc.body}", file=sys.stderr)
            except httpx.HTTPError as exc:
                print(f"  {label}: erro da API do Talk: {exc}", file=sys.stderr)
        print(
            "\nO Zep processa em segundo plano; para lotes grandes conte alguns minutos por contato."
        )
        return 0
    finally:
        await reader.close()
        await store.close()
        await close_zep()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90, help="0 = sem limite de data")
    parser.add_argument("--state", choices=["Open", "Closed", "All"], default="All")
    parser.add_argument("--chat", help="um chat especifico")
    parser.add_argument("--limit", type=int, default=0, help="maximo de chats a processar")
    parser.add_argument("--dry-run", action="store_true", help="so le do Talk, nao escreve no Zep")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.days, args.state, args.chat, args.dry_run, args.limit)))
