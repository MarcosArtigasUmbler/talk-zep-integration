"""Modelos do webhook do Umbler Talk.

Formato da chamada (help.umbler.com > Como criar Webhooks):

    {
      "Type": "Message",                 # EventWebhookType
      "EventDate": "2024-02-07T18:44:01.3135533Z",
      "EventId": "ZcPPcWpimiD3EiER",     # persiste entre re-tentativas
      "Payload": { "Type": "Chat", "Content": { ...BasicChatModel... } }
    }

O exemplo oficial usa PascalCase e o swagger da API (BasicChatModel) usa
camelCase. Para nao depender disso, o JSON e normalizado para chaves em
minusculas antes da validacao e todos os aliases aqui estao em minusculas.

Tipos de evento (enum EventWebhookType do swagger): Message, MemberTransfer,
ChatClosed, ChatSectorChanged, ChatPrivateStatusChanged, MessageFileUploaded,
ChatTagChanged, NewChat.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

EVENT_TYPES = (
    "Message",
    "MemberTransfer",
    "ChatClosed",
    "ChatSectorChanged",
    "ChatPrivateStatusChanged",
    "MessageFileUploaded",
    "ChatTagChanged",
    "NewChat",
)

# Tipos de contato do Talk. Os dois ultimos sao conversas internas entre membros.
INTERNAL_CONTACT_TYPES = {"memberdirectmessage", "membergroup"}
GROUP_CONTACT_TYPES = {"group", "membergroup"}


def lower_keys(obj: Any) -> Any:
    """Normaliza recursivamente as chaves de dicts para minusculas."""
    if isinstance(obj, dict):
        return {str(k).lower(): lower_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [lower_keys(v) for v in obj]
    return obj


_FRACTION = re.compile(r"(\.\d{1,6})\d*")


def parse_utc(value: str | None) -> datetime | None:
    """Converte o ISO 8601 do Talk (ate 7 casas decimais, sufixo Z) em datetime UTC."""
    if not value:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_rfc3339(value: str | datetime | None) -> str | None:
    """Formato aceito pelo Zep em created_at / valid_at."""
    parsed = value if isinstance(value, datetime) else parse_utc(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TalkModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class Ref(TalkModel):
    id: str | None = None
    name: str | None = None


class Tag(Ref):
    emoji: str | None = None


class Contact(Ref):
    phone_number: str | None = Field(default=None, alias="phonenumber")
    contact_type: str | None = Field(default=None, alias="contacttype")
    tags: list[Tag] = Field(default_factory=list)
    is_blocked: bool | None = Field(default=None, alias="isblocked")
    last_active_utc: str | None = Field(default=None, alias="lastactiveutc")

    @property
    def is_internal(self) -> bool:
        return (self.contact_type or "").lower() in INTERNAL_CONTACT_TYPES

    @property
    def is_group(self) -> bool:
        return (self.contact_type or "").lower() in GROUP_CONTACT_TYPES


class Channel(Ref):
    phone_number: str | None = Field(default=None, alias="phonenumber")
    kind: str | None = Field(default=None, alias="_t")


class Sector(Ref):
    default: bool | None = None


class Member(Ref):
    """ChatAgentReferenceModel: no webhook vem so o id do atendente."""

    display_name: str | None = Field(default=None, alias="displayname")


class FileInfo(TalkModel):
    url: str | None = None
    content_type: str | None = Field(default=None, alias="contenttype")
    original_name: str | None = Field(default=None, alias="originalname")
    caption: str | None = None
    transcription: str | None = None


class Location(TalkModel):
    latitude: str | None = None
    longitude: str | None = None
    name: str | None = None
    address: str | None = None


class ContactCard(TalkModel):
    name: str | None = None
    company: str | None = None
    phone_numbers: list[str] = Field(default_factory=list, alias="phonenumbers")
    emails: list[str] = Field(default_factory=list)


class Button(TalkModel):
    text: str | None = None
    type: str | None = None
    selected: bool | None = None


class Message(TalkModel):
    id: str | None = None
    event_at_utc: str | None = Field(default=None, alias="eventatutc")
    created_at_utc: str | None = Field(default=None, alias="createdatutc")
    prefix: str | None = None
    header_content: str | None = Field(default=None, alias="headercontent")
    content: str | None = None
    footer: str | None = None
    message_type: str | None = Field(default=None, alias="messagetype")
    source: str | None = None
    is_private: bool = Field(default=False, alias="isprivate")
    message_state: str | None = Field(default=None, alias="messagestate")
    sent_by_organization_member: Ref | None = Field(default=None, alias="sentbyorganizationmember")
    from_contact: Ref | None = Field(default=None, alias="fromcontact")
    file: FileInfo | None = None
    location: Location | None = None
    contacts: list[ContactCard] = Field(default_factory=list)
    buttons: list[Button] = Field(default_factory=list)
    bot_instance: Ref | None = Field(default=None, alias="botinstance")
    template_id: str | None = Field(default=None, alias="templateid")

    @property
    def source_lower(self) -> str:
        return (self.source or "").lower()

    @property
    def type_lower(self) -> str:
        return (self.message_type or "text").lower()

    @property
    def happened_at(self) -> str | None:
        return self.event_at_utc or self.created_at_utc


class Chat(TalkModel):
    """BasicChatModel, com os campos que interessam a memoria."""

    id: str | None = None
    created_at_utc: str | None = Field(default=None, alias="createdatutc")
    event_at_utc: str | None = Field(default=None, alias="eventatutc")
    contact: Contact | None = None
    channel: Channel | None = None
    sector: Sector | None = None
    organization_member: Member | None = Field(default=None, alias="organizationmember")
    organization_members: list[Member] = Field(default_factory=list, alias="organizationmembers")
    tags: list[Tag] = Field(default_factory=list)
    last_message: Message | None = Field(default=None, alias="lastmessage")
    message: Message | None = None
    open: bool | None = None
    private: bool | None = None
    waiting: bool | None = None
    closed_at_utc: str | None = Field(default=None, alias="closedatutc")
    closed_by: Ref | None = Field(default=None, alias="closedby")

    @property
    def current_message(self) -> Message | None:
        """``message`` quando o evento traz a mensagem explicita; senao ``lastMessage``."""
        return self.message or self.last_message


class Payload(TalkModel):
    type: str | None = None
    content: Chat | None = None


class WebhookEvent(TalkModel):
    type: str
    event_date: str | None = Field(default=None, alias="eventdate")
    event_id: str = Field(alias="eventid")
    payload: Payload | None = None

    @property
    def chat(self) -> Chat | None:
        return self.payload.content if self.payload else None

    @property
    def type_lower(self) -> str:
        return self.type.lower()

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> WebhookEvent:
        return cls.model_validate(lower_keys(raw))


def peek_envelope(raw: Any) -> tuple[str | None, str | None, str | None]:
    """Le (event_id, type, event_date) sem validar o payload inteiro.

    Usado no handler HTTP, que precisa responder em menos de 5 segundos e nao
    deve rejeitar um evento por um campo interno inesperado.
    """
    if not isinstance(raw, dict):
        return None, None, None
    low = {str(k).lower(): v for k, v in raw.items()}
    eid = low.get("eventid")
    etype = low.get("type")
    edate = low.get("eventdate")
    return (
        str(eid) if eid is not None else None,
        str(etype) if etype is not None else None,
        str(edate) if edate is not None else None,
    )
