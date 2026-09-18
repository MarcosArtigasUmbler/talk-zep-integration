from app.talk.models import WebhookEvent, lower_keys, parse_utc, peek_envelope, to_rfc3339
from tests.conftest import sample_event


def test_parse_pascal_case_envelope_and_chat():
    ev = WebhookEvent.parse(sample_event())
    assert ev.event_id == "evt_msg_1"
    assert ev.type_lower == "message"
    chat = ev.chat
    assert chat is not None
    assert chat.id == "chat_1"
    assert chat.contact.name == "Carla Mendes"
    assert chat.contact.phone_number == "+5551999990000"
    assert chat.sector.name == "Vendas"
    assert chat.organization_member.id == "m1"
    assert chat.channel.phone_number == "+5551333330000"
    assert [t.name for t in chat.tags] == ["Proposta enviada"]
    assert chat.current_message.content.startswith("Oi, quero")


def test_parse_camel_case_like_swagger():
    raw = {
        "type": "ChatClosed",
        "eventDate": "2026-09-18T12:00:00Z",
        "eventId": "evt_2",
        "payload": {
            "type": "Chat",
            "content": {
                "id": "chat_9",
                "contact": {
                    "id": "ct_9",
                    "name": "Diego",
                    "phoneNumber": "+55",
                    "contactType": "DirectMessage",
                },
                "sector": {"id": "s", "name": "Suporte"},
                "open": False,
                "closedAtUTC": "2026-09-18T12:00:00Z",
                "closedBy": {"id": "m2"},
                "lastMessage": None,
            },
        },
    }
    ev = WebhookEvent.parse(raw)
    assert ev.type_lower == "chatclosed"
    assert ev.chat.open is False
    assert ev.chat.closed_by.id == "m2"
    assert ev.chat.current_message is None


def test_unknown_fields_are_ignored():
    ev = WebhookEvent.parse(sample_event(content={"TotalAIResponses": 3, "Bots": [{"BotId": "x"}]}))
    assert ev.chat.id == "chat_1"


def test_lower_keys_recursive():
    assert lower_keys({"A": [{"B": 1}], "c": {"D": None}}) == {"a": [{"b": 1}], "c": {"d": None}}


def test_parse_utc_handles_seven_fraction_digits():
    dt = parse_utc("2024-02-07T18:44:01.3135533Z")
    assert dt is not None
    assert dt.year == 2024 and dt.microsecond == 313553
    assert to_rfc3339("2024-02-07T18:44:01.3135533Z") == "2024-02-07T18:44:01.313Z"
    assert to_rfc3339("2024-02-07T18:44:01Z") == "2024-02-07T18:44:01.000Z"
    assert parse_utc("nao e data") is None
    assert parse_utc(None) is None


def test_peek_envelope_is_case_insensitive():
    assert peek_envelope({"eventId": "x", "TYPE": "Message", "EventDate": "d"}) == (
        "x",
        "Message",
        "d",
    )
    assert peek_envelope([1, 2]) == (None, None, None)
    assert peek_envelope({"Payload": {}}) == (None, None, None)
