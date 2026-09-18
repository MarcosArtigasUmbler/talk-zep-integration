from app.pipeline.store import STATUS_DONE, STATUS_FAILED, fact_key


async def test_event_idempotency(store):
    assert await store.record_event("e1", "Message", "d", {"a": 1}) is True
    assert await store.record_event("e1", "Message", "d", {"a": 1}) is False
    row = await store.get_event("e1")
    assert row["status"] == "pending" and row["attempts"] == 0
    assert await store.pending_event_ids() == ["e1"]

    await store.mark("e1", "processing", bump_attempts=True)
    await store.mark("e1", STATUS_DONE, summary="ok")
    row = await store.get_event("e1")
    assert (row["status"], row["attempts"], row["summary"]) == (STATUS_DONE, 1, "ok")
    assert await store.pending_event_ids() == []

    await store.mark("e1", STATUS_FAILED, error="boom")
    assert await store.reset_event("e1") is True
    assert (await store.get_event("e1"))["status"] == "pending"
    assert await store.reset_event("nope") is False
    assert (await store.stats())["events_pending"] == 1


async def test_messages_facts_and_contacts(store):
    assert await store.message_seen("m1") is False
    await store.mark_messages([("m1", "e1", "t1"), ("m2", "e1", "t1")])
    assert await store.message_seen("m1") is True

    k = fact_key("u", "TEM_TAG", "Lead", "u tem a tag Lead")
    assert k == fact_key("u", "TEM_TAG", "lead", "U TEM A TAG LEAD")  # case-insensitive
    assert await store.fact_seen(k) is False
    await store.mark_fact(k, "u", "TEM_TAG", "u tem a tag Lead")
    assert await store.fact_seen(k) is True

    assert await store.contact_fingerprint("u") is None
    await store.upsert_contact("u", "c", "Nome", "+55", "fp1")
    assert await store.contact_fingerprint("u") == "fp1"
    await store.upsert_contact("u", "c", "Nome 2", "+55", "fp2")
    assert (await store.get_contact("u"))["name"] == "Nome 2"
    assert len(await store.list_contacts()) == 1


async def test_chats_and_latest_thread(store):
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
    # COALESCE mantem valores antigos quando o novo evento nao traz
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
    assert rows[0]["chat_id"] == "c1" and rows[0]["sector"] == "Vendas" and rows[0]["open"] == 1


async def test_members(store):
    await store.set_member_names({"m1": "Rafael"})
    assert await store.member_name("m1") == "Rafael"
    await store.set_member_names({"m1": "Rafael Torres"})
    assert await store.all_member_names() == {"m1": "Rafael Torres"}
