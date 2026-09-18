"""Transforma um evento do Talk em escritas no Zep.

Dois caminhos de escrita, de proposito (APRENDIZADOS-ZEP.md, secao 9):

* **Mensagens** sao material para interpretar -> ``thread.add_messages``.
* **Dado estruturado** que o webhook permite afirmar (quem atende o contato)
  -> ``graph.add_fact_triple``, sem LLM.

Estado operacional (setor, canal, tags, chat aberto/fechado) **nao vira no do
grafo**: fica no Store e na metadata do usuario e das mensagens, e o
``/briefing`` devolve o estado atual. Tags sao muitas e volateis; no grafo
virariam fatos defasados competindo com fatos uteis.
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

MESSAGE_EVENTS = {"message", "messagefileuploaded"}


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

        # 1. usuario (ficha do contato, tags incluidas) -- so se mudou
        fingerprint = contact_fingerprint(contact)
        previous = await self.store.contact_fingerprint(user_id)
        if previous != fingerprint:
            result = await ensure_user(user_id, contact, update=previous is not None)
            await self.store.upsert_contact(
                user_id,
                contact.id or "",
                contact.name,
                contact.phone_number,
                fingerprint,
                tags=[t.name for t in contact.tags if t.name],
            )
            out.add(f"usuario {result}")

        # 2. thread (chat) + estado operacional
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

        # 3. fato estruturado: quem atende
        if member_id:
            name = await self.members.name_for(member_id)
            label = self.members.label_for(member_id, name)
            origem = "transferencia" if event.type_lower == "membertransfer" else "atendimento"
            if await self._send_fact(
                user_id, F.fact_atendido_por(contact_name, label, when, origem)
            ):
                out.add(f"atendido por {label}")

        # 4. mensagem
        if event.type_lower in MESSAGE_EVENTS:
            out.add(await self._handle_message(event, chat, user_id, thread_id, event_id))

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
