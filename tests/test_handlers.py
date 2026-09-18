from app.pipeline.handlers import EventHandler
from app.talk.models import WebhookEvent
from tests.conftest import sample_event


async def handle(handler, raw):
    ev = WebhookEvent.parse(raw)
    return await handler.handle(ev, event_id=ev.event_id)


async def test_message_event_creates_user_thread_facts_and_message(
    store, settings, members, fake_zep
):
    handler = EventHandler(store, settings, members)
    out = await handle(handler, sample_event())
    assert out.status == "done"

    assert fake_zep.of("user.add")[0]["user_id"] == "talk_contact_ct_1"
    assert fake_zep.of("user.add")[0]["first_name"] == "Carla"
    assert fake_zep.of("user.add")[0]["last_name"] == "Mendes"
    assert fake_zep.of("thread.create")[0] == {
        "thread_id": "talk_chat_chat_1",
        "user_id": "talk_contact_ct_1",
    }

    facts = fake_zep.of("graph.add_fact_triple")
    names = sorted(f["fact_name"] for f in facts)
    assert names == [
        "ATENDIDO_NO_SETOR",
        "ATENDIDO_POR",
        "CONTATO_PELO_CANAL",
        "TEM_TAG",
        "TEM_TAG",
    ]
    por = next(f for f in facts if f["fact_name"] == "ATENDIDO_POR")
    assert por["target_node_name"] == "Rafael Torres"
    assert por["source_node_labels"] == ["User"] and por["target_node_labels"] == ["Vendedor"]
    assert por["valid_at"] == "2026-09-18T12:00:00.000Z"
    assert all(f["user_id"] == "talk_contact_ct_1" for f in facts)

    adds = fake_zep.of("thread.add_messages")
    assert len(adds) == 1
    (thread_id,) = adds[0]["_args"]
    assert thread_id == "talk_chat_chat_1"
    m = adds[0]["messages"][0]
    assert (m.role, m.name) == ("user", "Carla Mendes")
    assert m.content == "Oi, quero saber do plano Professional"
    assert m.created_at == "2026-09-18T12:00:00.000Z"
    assert adds[0]["strict_ontology"] is None
    assert await store.message_seen("msg_1")


async def test_repeated_event_sends_nothing_new(store, settings, members, fake_zep):
    handler = EventHandler(store, settings, members)
    await handle(handler, sample_event())
    before = len(fake_zep.calls)
    out = await handle(handler, sample_event(EventId="evt_msg_1_retry"))
    assert out.status == "done"
    assert "ja enviada" in out.summary
    # sem novos fatos, sem novo usuario/thread, sem nova mensagem
    assert len(fake_zep.calls) == before


async def test_member_reply_and_private_note(store, settings, members, fake_zep):
    handler = EventHandler(store, settings, members)
    reply = sample_event(
        EventId="evt_2",
        message={
            "Id": "msg_2",
            "EventAtUTC": "2026-09-18T12:01:00Z",
            "Content": "Claro, Carla! O Professional custa...",
            "MessageType": "Text",
            "Source": "Member",
            "IsPrivate": False,
            "SentByOrganizationMember": {"Id": "m1"},
        },
    )
    await handle(handler, reply)
    m = fake_zep.of("thread.add_messages")[-1]["messages"][0]
    assert (m.role, m.name) == ("assistant", "Rafael Torres")

    note = sample_event(
        EventId="evt_3",
        message={
            "Id": "msg_3",
            "EventAtUTC": "2026-09-18T12:02:00Z",
            "Content": "Sócio decide sexta; ligar quinta.",
            "MessageType": "Text",
            "Source": "Member",
            "IsPrivate": True,
            "SentByOrganizationMember": {"Id": "m1"},
        },
    )
    out = await handle(handler, note)
    assert "nota interna" in out.summary
    add = fake_zep.of("graph.add")[-1]
    assert add["user_id"] == "talk_contact_ct_1" and add["type"] == "text"
    assert "Rafael Torres" in add["data"] and "ligar quinta" in add["data"]
    assert len(fake_zep.of("thread.add_messages")) == 1  # nota nao vira mensagem
    assert await store.message_seen("msg_3")


async def test_transfer_sector_change_and_close(store, settings, members, fake_zep):
    handler = EventHandler(store, settings, members)
    await handle(handler, sample_event())
    n0 = len(fake_zep.of("graph.add_fact_triple"))

    await handle(
        handler,
        sample_event(
            EventId="evt_t", Type="MemberTransfer", content={"OrganizationMember": {"Id": "m9"}}
        ),
    )
    novo = fake_zep.of("graph.add_fact_triple")[n0:]
    assert [f["fact_name"] for f in novo] == ["ATENDIDO_POR"]
    assert novo[0]["target_node_name"] == "Atendente m9"
    assert novo[0]["edge_attributes"] == {"origem": "transferencia"}

    await handle(
        handler,
        sample_event(
            EventId="evt_s",
            Type="ChatSectorChanged",
            content={"Sector": {"Id": "s2", "Name": "Suporte"}},
        ),
    )
    assert fake_zep.of("graph.add_fact_triple")[-1]["fact_name"] == "ATENDIDO_NO_SETOR"
    assert fake_zep.of("graph.add_fact_triple")[-1]["target_node_name"] == "Suporte"

    closed = sample_event(
        EventId="evt_c",
        Type="ChatClosed",
        content={"Open": False, "ClosedAtUTC": "2026-09-18T13:00:00Z", "ClosedBy": {"Id": "m1"}},
    )
    out = await handle(handler, closed)
    last = fake_zep.of("graph.add_fact_triple")[-1]
    assert last["fact_name"] == "ATENDIMENTO_ENCERRADO"
    assert "Rafael Torres" in last["fact"] and "2026-09-18" in last["fact"]
    # ChatClosed repete o LastMessage no payload, mas nao e evento de mensagem
    assert len(fake_zep.of("thread.add_messages")) == 1
    assert out.summary == "1 fato(s)"
    threads = await store.threads_for_user("talk_contact_ct_1")
    assert threads[0]["open"] == 0


async def test_contact_profile_change_updates_user(store, settings, members, fake_zep):
    handler = EventHandler(store, settings, members)
    await handle(handler, sample_event())
    await handle(
        handler,
        sample_event(
            EventId="evt_n",
            Type="ChatTagChanged",
            content={
                "Contact": {
                    "Id": "ct_1",
                    "Name": "Carla Mendes Souza",
                    "PhoneNumber": "+5551999990000",
                    "ContactType": "DirectMessage",
                    "Tags": [{"Id": "t1", "Name": "Lead quente"}, {"Id": "t3", "Name": "Cliente"}],
                }
            },
        ),
    )
    upd = fake_zep.of("user.update")
    assert len(upd) == 1 and upd[0]["last_name"] == "Mendes Souza"
    tags = [
        f["target_node_name"]
        for f in fake_zep.of("graph.add_fact_triple")
        if f["fact_name"] == "TEM_TAG"
    ]
    assert tags.count("Cliente") == 1


async def test_internal_and_group_chats_are_skipped(store, settings, members, fake_zep):
    handler = EventHandler(store, settings, members)
    out = await handle(
        handler,
        sample_event(
            content={"Contact": {"Id": "ct_i", "Name": "Equipe", "ContactType": "MemberGroup"}}
        ),
    )
    assert out.status == "skipped" and "interna" in out.summary
    out = await handle(
        handler,
        sample_event(content={"Contact": {"Id": "ct_g", "Name": "Grupo", "ContactType": "Group"}}),
    )
    assert out.status == "skipped" and "grupo" in out.summary
    assert fake_zep.calls == []


async def test_private_notes_can_be_disabled(store, settings, members, fake_zep):
    settings = settings.model_copy(update={"ingest_private_notes": False})
    handler = EventHandler(store, settings, members)
    note = sample_event(
        message={
            "Id": "msg_p",
            "Content": "nota",
            "MessageType": "Text",
            "Source": "Member",
            "IsPrivate": True,
        }
    )
    out = await handle(handler, note)
    assert "ignorada" in out.summary
    assert fake_zep.of("graph.add") == []
    assert await store.message_seen("msg_p")
