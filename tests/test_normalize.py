from app.talk.models import Chat, Message, WebhookEvent, lower_keys
from app.talk.normalize import (
    chat_thread_id,
    contact_id_from_user,
    contact_user_id,
    display_name,
    message_text,
    normalize_message,
    should_ingest_chat,
    split_name,
)
from tests.conftest import sample_event


def msg(**kw) -> Message:
    base = {"Id": "m", "EventAtUTC": "2026-09-18T12:00:00Z", "Source": "Contact"}
    base.update(kw)
    return Message.model_validate(lower_keys(base))


def test_ids_are_stable_and_reversible():
    assert contact_user_id("abc") == "talk_contact_abc"
    assert chat_thread_id("xyz") == "talk_chat_xyz"
    assert contact_id_from_user("talk_contact_abc") == "abc"


def test_display_and_split_name():
    chat = WebhookEvent.parse(sample_event()).chat
    assert display_name(chat.contact) == "Carla Mendes"
    assert split_name("Carla Mendes de Souza") == ("Carla", "Mendes de Souza")
    assert split_name("Carla") == ("Carla", None)
    chat.contact.name = None
    assert display_name(chat.contact) == "Contato +5551999990000"


def test_should_ingest_filters_internal_and_groups():
    chat = WebhookEvent.parse(sample_event()).chat
    assert should_ingest_chat(chat, ingest_groups=False) == (True, "ok")
    chat.contact.contact_type = "MemberDirectMessage"
    assert should_ingest_chat(chat, ingest_groups=True)[0] is False
    chat.contact.contact_type = "Group"
    assert should_ingest_chat(chat, ingest_groups=False)[0] is False
    assert should_ingest_chat(chat, ingest_groups=True)[0] is True
    assert should_ingest_chat(None, ingest_groups=True)[0] is False


def test_message_text_by_type():
    assert message_text(msg(Content="olá", MessageType="Text")) == "olá"
    assert message_text(msg(MessageType="Reaction", Content="👍")) is None
    assert message_text(msg(MessageType="Unsupported")) is None
    assert (
        message_text(msg(MessageType="Image", File={"Caption": "print do erro"}))
        == "[imagem] print do erro"
    )
    assert message_text(msg(MessageType="Image")) == "[imagem]"
    assert (
        message_text(msg(MessageType="Audio", File={"Transcription": "quero cancelar"}))
        == "[áudio transcrito] quero cancelar"
    )
    assert message_text(msg(MessageType="Audio")) == "[áudio sem transcrição]"
    assert (
        message_text(msg(MessageType="File", File={"OriginalName": "contrato.pdf"}))
        == "[arquivo: contrato.pdf]"
    )
    assert (
        message_text(
            msg(
                MessageType="Location",
                Location={
                    "Name": "Umbler",
                    "Address": "Porto Alegre",
                    "Latitude": "-30",
                    "Longitude": "-51",
                },
            )
        )
        == "[localização] Umbler, Porto Alegre"
    )
    assert (
        message_text(
            msg(MessageType="Contact", Contacts=[{"Name": "João", "PhoneNumbers": ["+55"]}])
        )
        == "[contato compartilhado] João +55"
    )
    assert message_text(msg(MessageType="CallAttempt")) == "[tentativa de ligação pelo WhatsApp]"
    assert (
        message_text(msg(MessageType="ButtonReply", Buttons=[{"Text": "Quero", "Selected": True}]))
        == "[opção escolhida] Quero"
    )
    assert (
        message_text(
            msg(MessageType="Text", HeaderContent="Proposta", Content="corpo", Footer="Umbler")
        )
        == "Proposta\ncorpo\nUmbler"
    )
    assert message_text(msg(MessageType="Text", Content="   ")) is None


def test_normalize_roles_and_names():
    chat = WebhookEvent.parse(sample_event()).chat
    contact_msg = normalize_message(
        chat.current_message, chat, member_name="Rafael", event_date=None
    )
    assert contact_msg.role == "user"
    assert contact_msg.name == "Carla Mendes"
    assert contact_msg.created_at == "2026-09-18T12:00:00.000Z"
    assert contact_msg.metadata["talk_message_id"] == "msg_1"
    assert contact_msg.metadata["sector"] == "Vendas"

    member = msg(Source="Member", Content="Claro!", SentByOrganizationMember={"Id": "m1"})
    n = normalize_message(member, chat, member_name="Rafael Torres", event_date=None)
    assert (n.role, n.name) == ("assistant", "Rafael Torres")
    n = normalize_message(member, chat, member_name=None, event_date=None)
    assert n.name == "Atendente Umbler"

    bot = msg(Source="Bot", Content="Bem-vindo", BotInstance={"Id": "b", "Name": "Triagem"})
    n = normalize_message(bot, chat, member_name=None, event_date=None)
    assert (n.role, n.name) == ("assistant", "Triagem")

    private = msg(Source="Member", Content="cliente irritado", IsPrivate=True)
    assert normalize_message(private, chat, member_name="R", event_date=None).is_private is True

    assert (
        normalize_message(msg(MessageType="Reaction"), chat, member_name=None, event_date=None)
        is None
    )
    assert normalize_message(msg(Id=None), chat, member_name=None, event_date=None) is None


def test_created_at_falls_back_to_event_date():
    chat = Chat.model_validate({"id": "c", "contact": {"id": "x", "name": "N"}})
    m = msg(Content="oi", EventAtUTC=None)
    n = normalize_message(m, chat, member_name=None, event_date="2026-01-01T00:00:00Z")
    assert n.created_at == "2026-01-01T00:00:00.000Z"


def test_long_message_is_truncated():
    chat = Chat.model_validate({"id": "c", "contact": {"id": "x", "name": "N"}})
    n = normalize_message(msg(Content="a" * 5000), chat, member_name=None, event_date=None)
    assert len(n.content) == 4000
