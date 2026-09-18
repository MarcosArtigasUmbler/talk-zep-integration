"""Traduz o que vem do Talk para o vocabulario do Zep.

Decisoes de modelagem (ver APRENDIZADOS-ZEP.md, secao 2):

* O **usuario do Zep e o contato** (lead/cliente), nunca o atendente.
  ``user_id = talk_contact_<id do contato no Talk>`` -- ID estavel; o telefone
  nao serve porque o cliente troca.
* Cada chat do Talk vira uma **thread** do Zep: ``talk_chat_<id do chat>``.
  Todas as threads de um contato alimentam o mesmo grafo.
* Mensagem do contato -> role ``user``; mensagem de membro, bot ou externa ->
  role ``assistant``. Notas internas (``IsPrivate``) nao sao conversa: entram
  como episodio de texto no grafo do contato.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.talk.models import Chat, Contact, Message, to_rfc3339

USER_PREFIX = "talk_contact_"
THREAD_PREFIX = "talk_chat_"

# Tipos de mensagem que nao carregam informacao util para a memoria.
SKIPPED_TYPES = {"reaction", "unsupported"}

# Limite do Zep: 4.096 caracteres por mensagem.
MESSAGE_MAX_CHARS = 4000


def contact_user_id(contact_id: str) -> str:
    return f"{USER_PREFIX}{contact_id}"


def chat_thread_id(chat_id: str) -> str:
    return f"{THREAD_PREFIX}{chat_id}"


def contact_id_from_user(user_id: str) -> str:
    return user_id.removeprefix(USER_PREFIX)


def display_name(contact: Contact | None) -> str:
    if contact and contact.name and contact.name.strip():
        return contact.name.strip()
    if contact and contact.phone_number:
        return f"Contato {contact.phone_number}"
    return "Contato"


def split_name(name: str) -> tuple[str, str | None]:
    parts = name.split()
    if not parts:
        return name, None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], " ".join(parts[1:])


def should_ingest_chat(chat: Chat | None, *, ingest_groups: bool) -> tuple[bool, str]:
    """Filtra conversas que nao sao atendimento a um contato externo."""
    if chat is None or chat.contact is None or not chat.contact.id:
        return False, "evento sem contato"
    if chat.contact.is_internal:
        return False, "conversa interna entre membros"
    if chat.contact.is_group and not ingest_groups:
        return False, "conversa de grupo (INGEST_GROUP_CHATS=false)"
    if not chat.id:
        return False, "evento sem id de chat"
    return True, "ok"


def _trim(text: str | None) -> str:
    return (text or "").strip()


def message_text(msg: Message) -> str | None:
    """Extrai um texto legivel de qualquer tipo de mensagem do Talk.

    Devolve None quando nao ha nada que valha a pena guardar.
    """
    kind = msg.type_lower
    if kind in SKIPPED_TYPES:
        return None

    content = _trim(msg.content)
    parts: list[str] = []

    if kind in {"image", "video", "gif", "sticker"}:
        label = {"image": "imagem", "video": "vídeo", "gif": "gif", "sticker": "figurinha"}[kind]
        caption = _trim(msg.file.caption) if msg.file else ""
        parts.append(f"[{label}]" + (f" {caption}" if caption else ""))
        if content and content != caption:
            parts.append(content)
    elif kind == "audio":
        transcription = _trim(msg.file.transcription) if msg.file else ""
        if transcription:
            parts.append(f"[áudio transcrito] {transcription}")
        else:
            parts.append("[áudio sem transcrição]")
        if content:
            parts.append(content)
    elif kind == "file":
        name = _trim(msg.file.original_name) if msg.file else ""
        caption = _trim(msg.file.caption) if msg.file else ""
        label = f"[arquivo: {name}]" if name else "[arquivo]"
        parts.append(label + (f" {caption}" if caption else ""))
        if content and content != caption:
            parts.append(content)
    elif kind == "location":
        loc = msg.location
        if loc:
            where = ", ".join(p for p in (loc.name, loc.address) if p)
            coords = f"{loc.latitude},{loc.longitude}" if loc.latitude and loc.longitude else ""
            parts.append(f"[localização] {where or coords}".strip())
        if content:
            parts.append(content)
    elif kind == "contact":
        cards = []
        for card in msg.contacts:
            bits = [card.name or "", card.company or ""] + list(card.phone_numbers)
            cards.append(" ".join(b for b in bits if b))
        parts.append("[contato compartilhado] " + "; ".join(c for c in cards if c))
        if content:
            parts.append(content)
    elif kind == "callattempt":
        parts.append("[tentativa de ligação pelo WhatsApp]")
    elif kind in {"listreply", "buttonreply", "quickreply"}:
        chosen = next((b.text for b in msg.buttons if b.selected and b.text), None)
        parts.append(content or (f"[opção escolhida] {chosen}" if chosen else ""))
    elif kind in {"order", "payment"}:
        parts.append(content or f"[{kind}]")
    else:
        # Text, Url, List, Poll, Carousel, templates...
        header = _trim(msg.header_content)
        footer = _trim(msg.footer)
        body = "\n".join(p for p in (header, content, footer) if p)
        if body:
            parts.append(body)
        options = [b.text for b in msg.buttons if b.text]
        if options and kind in {"list", "carousel"}:
            parts.append("Opções: " + " | ".join(options))

    text = "\n".join(p for p in parts if p).strip()
    return text or None


@dataclass
class NormalizedMessage:
    message_id: str
    role: str
    name: str
    content: str
    created_at: str | None
    is_private: bool
    source: str
    message_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_message(
    msg: Message,
    chat: Chat,
    *,
    member_name: str | None,
    event_date: str | None,
) -> NormalizedMessage | None:
    """Monta a mensagem no formato do Zep, ou None se nao ha o que guardar."""
    if not msg.id:
        return None
    text = message_text(msg)
    if not text:
        return None

    contact_name = display_name(chat.contact)
    source = msg.source_lower or ("member" if msg.sent_by_organization_member else "contact")
    if source == "contact":
        role, name = "user", contact_name
    elif source == "bot":
        bot_name = msg.bot_instance.name if msg.bot_instance and msg.bot_instance.name else None
        role, name = "assistant", (bot_name or "Bot Umbler Talk")
    else:  # member, external
        role, name = "assistant", (member_name or "Atendente Umbler")

    created_at = to_rfc3339(msg.happened_at) or to_rfc3339(event_date)
    metadata: dict[str, Any] = {
        "talk_message_id": msg.id,
        "talk_chat_id": chat.id or "",
        "source": source,
        "message_type": msg.type_lower,
    }
    if chat.sector and chat.sector.name:
        metadata["sector"] = chat.sector.name
    if chat.channel and chat.channel.name:
        metadata["channel"] = chat.channel.name

    return NormalizedMessage(
        message_id=msg.id,
        role=role,
        name=name,
        content=text[:MESSAGE_MAX_CHARS],
        created_at=created_at,
        is_private=msg.is_private,
        source=source,
        message_type=msg.type_lower,
        metadata=metadata,
    )
