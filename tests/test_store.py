"""Contrato do Store, executado em cada backend disponivel (ver conftest)."""

from app.pipeline.store import STATUS_DONE, STATUS_FAILED, fact_key


async def test_event_idempotency(any_store):
    store = any_store
    assert await store.record_event("e1", "Message", "d", {"a": 1}) is True
    assert await store.record_event("e1", "Message", "d", {"a": 1}) is False
    row = await store.get_event("e1")
    assert row["event_id"] == "e1"
    assert row["status"] == "pending" and row["attempts"] == 0
    assert row["payload"] == {"a": 1}
    assert await store.pending_event_ids() == ["e1"]

    await store.mark("e1", "processing", bump_attempts=True)
    await store.mark("e1", STATUS_DONE, summary="ok")
    row = await store.get_event("e1")
    assert (row["status"], row["attempts"], row["summary"]) == (STATUS_DONE, 1, "ok")
    assert await store.pending_event_ids() == []

    await store.mark("e1", STATUS_FAILED, error="boom")
    assert (await store.get_event("e1"))["last_error"] == "boom"
    assert await store.reset_event("e1") is True
    assert (await store.get_event("e1"))["status"] == "pending"
    assert await store.reset_event("nope") is False

    listed = await store.list_events()
    assert [e["event_id"] for e in listed] == ["e1"]
    assert "payload" not in listed[0]
    assert await store.list_events(status="done") == []
    assert (await store.stats())["events_pending"] == 1
    assert await store.get_event("nope") is None


async def test_messages_and_facts(any_store):
    store = any_store
    assert await store.message_seen("m1") is False
    await store.mark_messages([("m1", "e1", "t1"), ("m2", "e1", "t1")])
    await store.mark_messages([("m1", "e2", "t1")])  # repetido nao quebra
    assert await store.message_seen("m1") is True
    assert (await store.stats())["messages"] == 2

    k = fact_key("u", "TEM_TAG", "Lead", "u tem a tag Lead")
    assert k == fact_key("u", "TEM_TAG", "lead", "U TEM A TAG LEAD")  # case-insensitive
    assert await store.fact_seen(k) is False
    await store.mark_fact(k, "u", "TEM_TAG", "u tem a tag Lead")
    await store.mark_fact(k, "u", "TEM_TAG", "u tem a tag Lead")
    assert await store.fact_seen(k) is True


async def test_contacts_with_tags(any_store):
    store = any_store
    assert await store.contact_fingerprint("u") is None
    assert await store.get_contact("u") is None
    await store.upsert_contact("u", "c", "Nome", "+55", "fp1", tags=["Lead quente"])
    assert await store.contact_fingerprint("u") == "fp1"
    await store.upsert_contact("u", "c", "Nome 2", "+55", "fp2", tags=["Cliente", "VIP"])
    contact = await store.get_contact("u")
    assert contact["user_id"] == "u"
    assert contact["name"] == "Nome 2"
    assert contact["tags"] == ["Cliente", "VIP"]
    await store.upsert_contact("v", "d", None, None, "fp3")
    assert (await store.get_contact("v"))["tags"] == []
    assert len(await store.list_contacts()) == 2


async def test_chats_and_latest_thread(any_store):
    store = any_store
    await store.upsert_chat(
        "c1",
        "u",
        "t1",
        created_at="2026-01-01",
        last_event_at="2026-01-01T00:00:00Z",
        open_=True,
        sector="Vendas",
        member_id="m1",
    )
    await store.upsert_chat(
        "c2",
        "u",
        "t2",
        created_at="2026-02-01",
        last_event_at="2026-02-01T00:00:00Z",
        open_=None,
        sector=None,
        member_id=None,
    )
    assert await store.chat_known("c1") and not await store.chat_known("c3")
    assert await store.latest_thread_for_user("u") == "t2"
    assert await store.latest_thread_for_user("ninguem") is None
    # None nao sobrescreve o que ja estava guardado
    await store.upsert_chat(
        "c1",
        "u",
        "t1",
        created_at=None,
        last_event_at="2026-03-01T00:00:00Z",
        open_=None,
        sector=None,
        member_id=None,
    )
    rows = await store.threads_for_user("u")
    assert [r["chat_id"] for r in rows] == ["c1", "c2"]
    assert rows[0]["sector"] == "Vendas" and rows[0]["open"] is True
    assert rows[0]["created_at"] == "2026-01-01" and rows[0]["member_id"] == "m1"
    assert rows[1]["open"] is None and rows[1]["sector"] is None
    # fechar o chat sobrescreve
    await store.upsert_chat(
        "c1",
        "u",
        "t1",
        created_at=None,
        last_event_at=None,
        open_=False,
        sector=None,
        member_id=None,
    )
    assert (await store.threads_for_user("u"))[0]["open"] is False


async def test_members(any_store):
    store = any_store
    assert await store.member_name("m1") is None
    await store.set_member_names({"m1": "Rafael"})
    assert await store.member_name("m1") == "Rafael"
    await store.set_member_names({"m1": "Rafael Torres", "m2": "Juliana"})
    assert await store.all_member_names() == {"m1": "Rafael Torres", "m2": "Juliana"}
    await store.set_member_names({})
