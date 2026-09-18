"""Transforma um evento do Talk em escritas no Zep.

Dois caminhos de escrita, de proposito (APRENDIZADOS-ZEP.md, secao 9):

* **Mensagens** sao material para interpretar -> ``thread.add_messages``.
* **Dados estruturados** do webhook (setor, atendente, tags, canal,
  encerramento) ja sao conhecidos -> ``graph.add_fact_triple``, sem LLM.

Cada evento, seja qual for o tipo, sincroniza a ficha (usuario), a thread e
os fatos estruturados; so o que mudou e enviado (deduplicado no SQLite).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.pipeline.store import Store, fact_key
from app.talk.members import MemberDirectory
from app.talk.models import Chat, WebhookEvent, to_rfc3339
from app.talk.normalize import (
    chat_thread_id,
    contact_user_id,
    display_name,
    normalize_message,
    should_ingest_chat,
)
from app.zep import facts as F
from app.zep.knowledge import add_contact_note
from app.zep.threads import add_live_messages, ensure_thread
from app.zep.users import contact_fingerprint, ensure_user

log = logging.getLogger(__name__)


@dataclass
class Outcome:
    status: str  # done | skipped
    notes: list[str] = field(default_factory=list)

    def add(self, note: str) -> None:
        self.notes.append(note)

    @property
    def summary(self) -> str:
        return "; ".join(self.notes) or self.status


class EventHandler:
    def __init__(self, store: Store, settings: Settings, members: MemberDirectory) -> None:
        self.store = store
        self.settings = settings
        self.members = members

    async def handle(self, event: WebhookEvent, *, event_id: str) -> Outcome:
        chat = event.chat
        ok, reason = should_ingest_chat(chat, ingest_groups=self.settings.ingest_group_chats)
        if not ok or chat is None or chat.contact is None:
            return Outcome("skipped", [reason])

        contact = chat.contact
        user_id = contact_user_id(contact.id or "")
        thread_id = chat_thread_id(chat.id or "")
        contact_name = display_name(contact)
        when = to_rfc3339(chat.event_at_utc) or to_rfc3339(event.event_date)
        out = Outcome("done")

        # 1. usuario (ficha do contato) -- so atualiza se mudou
        fingerprint = contact_fingerprint(contact)
        previous = await self.store.contact_fingerprint(user_id)
        if previous != fingerprint:
            result = await ensure_user(user_id, contact, update=previous is not None)
            await self.store.upsert_contact(
                user_id, contact.id or "", contact.name, contact.phone_number, fingerprint
            )
            out.add(f"usuario {result}")

        # 2. thread (chat)
        if not await self.store.chat_known(chat.id or ""):
            created = await ensure_thread(thread_id, user_id)
            out.add("thread criada" if created else "thread existente")
        member_id = chat.organization_member.id if chat.organization_member else None
        await self.store.upsert_chat(
            chat.id or "",
            user_id,
            thread_id,
            created_at=to_rfc3339(chat.created_at_utc),
            last_event_at=when,
            open_=chat.open,
            sector=chat.sector.name if chat.sector else None,
            member_id=member_id,
        )

        # 3. fatos estruturados (deduplicados)
        sent = await self._sync_structured_facts(
            user_id, contact_name, chat, when, event.type_lower
        )
        if sent:
            out.add(f"{sent} fato(s)")

        # 4. mensagem
        if event.type_lower in {"message", "messagefileuploaded"}:
            note = await self._handle_message(event, chat, user_id, thread_id, event_id)
            out.add(note)
        elif event.type_lower == "chatprivatestatuschanged":
            out.add("status privado ignorado")

        return out

    # --- mensagens -----------------------------------------------------------

    async def _handle_message(
        self, event: WebhookEvent, chat: Chat, user_id: str, thread_id: str, event_id: str
    ) -> str:
        msg = chat.current_message
        if msg is None or not msg.id:
            return "evento sem mensagem"
        if await self.store.message_seen(msg.id):
            return f"mensagem {msg.id} ja enviada"

        member_ref = msg.sent_by_organization_member or chat.organization_member
        member_name = await self.members.name_for(member_ref.id if member_ref else None)
        normalized = normalize_message(
            msg, chat, member_name=member_name, event_date=event.event_date
        )
        if normalized is None:
            await self.store.mark_messages([(msg.id, event_id, thread_id)])
            return f"mensagem {msg.type_lower} sem conteudo util"

        if normalized.is_private:
            if not self.settings.ingest_private_notes:
                await self.store.mark_messages([(msg.id, event_id, thread_id)])
                return "nota interna ignorada (INGEST_PRIVATE_NOTES=false)"
            author = member_name or self.members.label_for(
                member_ref.id if member_ref else None, None
            )
            text = (
                f"Nota interna de {author} sobre {display_name(chat.contact)} "
                f"no Umbler Talk: {normalized.content}"
            )
            await add_contact_note(
                user_id,
                text,
                source_description="Nota interna do atendente no Umbler Talk",
                created_at=normalized.created_at,
            )
            await self.store.mark_messages([(msg.id, event_id, thread_id)])
            return "nota interna gravada no grafo"

        await add_live_messages(
            thread_id, [normalized], strict_ontology=self.settings.zep_strict_ontology
        )
        await self.store.mark_messages([(msg.id, event_id, thread_id)])
        return f"mensagem {normalized.role}/{normalized.message_type} gravada"

    # --- fatos ---------------------------------------------------------------

    async def _send_fact(self, user_id: str, triple: F.FactTriple) -> bool:
        key = fact_key(user_id, triple.fact_name, triple.target_name, triple.fact)
        if await self.store.fact_seen(key):
            return False
        await F.add_fact(user_id, triple)
        await self.store.mark_fact(key, user_id, triple.fact_name, triple.fact)
        return True

    async def _sync_structured_facts(
        self, user_id: str, contact_name: str, chat: Chat, when: str | None, event_type: str
    ) -> int:
        sent = 0
        triples: list[F.FactTriple] = []

        if chat.channel and chat.channel.name:
            triples.append(
                F.fact_canal(contact_name, chat.channel.name, chat.channel.phone_number, when)
            )

        if chat.sector and chat.sector.name:
            triples.append(F.fact_setor(contact_name, chat.sector.name, when))

        if chat.organization_member and chat.organization_member.id:
            mid = chat.organization_member.id
            name = await self.members.name_for(mid)
            label = self.members.label_for(mid, name)
            origem = "transferencia" if event_type == "membertransfer" else "atendimento"
            triples.append(F.fact_atendido_por(contact_name, label, when, origem))

        for tag in chat.contact.tags if chat.contact else []:
            if tag.name:
                triples.append(F.fact_tag(contact_name, tag.name, when, "contato"))
        for tag in chat.tags:
            if tag.name:
                triples.append(F.fact_tag(contact_name, tag.name, when, "conversa"))

        if event_type == "chatclosed" or (chat.open is False and chat.closed_at_utc):
            sector = chat.sector.name if chat.sector and chat.sector.name else "Atendimento"
            closer = None
            if chat.closed_by and chat.closed_by.id:
                closer_name = await self.members.name_for(chat.closed_by.id)
                closer = self.members.label_for(chat.closed_by.id, closer_name)
            triples.append(
                F.fact_encerrado(
                    contact_name, sector, to_rfc3339(chat.closed_at_utc) or when, closer
                )
            )

        for triple in triples:
            if await self._send_fact(user_id, triple):
                sent += 1
        return sent
