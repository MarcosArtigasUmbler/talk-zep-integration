"""Configuracao dos testes: sem Zep real, sem worker, MongoDB real.

Nao ha banco em memoria: por decisao do projeto o Store e MongoDB em todos os
ambientes. Os testes usam a URI de ``MONGODB_TEST_URI`` ou, na falta, a
``MONGODB_URI`` do ``.env``, sempre num banco descartavel criado por sessao
(``talk_zep_test_<hex>``) e apagado no fim.
"""

from __future__ import annotations

import copy
import os
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from zep_cloud.core.api_error import ApiError

_ENV = dotenv_values(".env")
TEST_DB = f"talk_zep_test_{uuid.uuid4().hex[:8]}"
MONGODB_URI = (
    os.getenv("MONGODB_TEST_URI") or os.getenv("MONGODB_URI") or _ENV.get("MONGODB_URI") or ""
)
if not MONGODB_URI:
    raise SystemExit("testes precisam de MONGODB_TEST_URI ou MONGODB_URI (no ambiente ou .env)")

os.environ.update(
    {
        "ZEP_API_KEY": "test-key",
        "ZEP_ORG_GRAPH_ID": "umbler_teste",
        "WEBHOOK_TOKEN": "segredo",
        "MONGODB_URI": MONGODB_URI,
        "MONGODB_USERNAME": os.getenv("MONGODB_USERNAME") or _ENV.get("MONGODB_USERNAME") or "",
        "MONGODB_PASSWORD": os.getenv("MONGODB_PASSWORD") or _ENV.get("MONGODB_PASSWORD") or "",
        "MONGODB_DB": TEST_DB,
        "START_WORKER": "false",
        "MEMBERS_FILE": "",
        "LOG_LEVEL": "WARNING",
    }
)

from app.config import Settings, get_settings
from app.pipeline.store import MongoStore, create_store
from app.talk.members import MemberDirectory

COLLECTIONS = ("events", "messages", "facts", "contacts", "chats", "members")

SAMPLE_EVENT = {
    "Type": "Message",
    "EventDate": "2026-09-18T12:00:00.1234567Z",
    "EventId": "evt_msg_1",
    "Payload": {
        "Type": "Chat",
        "Content": {
            "Id": "chat_1",
            "CreatedAtUTC": "2026-09-18T11:55:00Z",
            "EventAtUTC": "2026-09-18T12:00:00Z",
            "Organization": {"Id": "org_1"},
            "Contact": {
                "Id": "ct_1",
                "Name": "Carla Mendes",
                "PhoneNumber": "+5551999990000",
                "ContactType": "DirectMessage",
                "Tags": [{"Id": "t1", "Name": "Lead quente"}],
            },
            "Channel": {
                "_t": "ChatCloudAPIWhatsappChannelReferenceModel",
                "Id": "ch_1",
                "Name": "WhatsApp Vendas",
                "PhoneNumber": "+5551333330000",
            },
            "Sector": {"Id": "s1", "Name": "Vendas", "Default": True},
            "OrganizationMember": {"Id": "m1"},
            "Tags": [{"Id": "t2", "Name": "Proposta enviada"}],
            "LastMessage": {
                "Id": "msg_1",
                "EventAtUTC": "2026-09-18T12:00:00Z",
                "Content": "Oi, quero saber do plano Professional",
                "MessageType": "Text",
                "Source": "Contact",
                "IsPrivate": False,
                "MessageState": "Received",
                "Chat": {"Id": "chat_1"},
            },
            "Open": True,
            "Private": False,
            "Waiting": True,
            "TotalUnread": 1,
        },
    },
}


def sample_event(**overrides) -> dict:
    """Copia do evento de exemplo, com ajustes no envelope e/ou no chat."""
    ev = copy.deepcopy(SAMPLE_EVENT)
    content = overrides.pop("content", None)
    message = overrides.pop("message", None)
    ev.update(overrides)
    if content:
        ev["Payload"]["Content"].update(content)
    if message is not None:
        ev["Payload"]["Content"]["LastMessage"] = message
    return ev


class _Calls:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def of(self, name: str) -> list[dict]:
        return [c[1] for c in self.calls if c[0] == name]


class FakeZep(_Calls):
    """Grava as chamadas ao SDK e simula os 409 de 'ja existe'."""

    def __init__(self) -> None:
        super().__init__()
        self.users: set[str] = set()
        self.threads: set[str] = set()
        self.user = SimpleNamespace(add=self._user_add, update=self._user_update)
        self.thread = SimpleNamespace(
            create=self._thread_create,
            add_messages=self._rec("thread.add_messages"),
            add_messages_batch=self._rec("thread.add_messages_batch"),
        )
        self.graph = SimpleNamespace(
            add=self._rec("graph.add"),
            add_batch=self._rec("graph.add_batch", result=[]),
            add_fact_triple=self._rec("graph.add_fact_triple"),
        )

    def _rec(self, name, result=None):
        async def fn(*args, **kwargs):
            payload = dict(kwargs)
            if args:
                payload["_args"] = args
            self.calls.append((name, payload))
            return result if result is not None else SimpleNamespace(uuid_="ep", processed=False)

        return fn

    async def _user_add(self, **kwargs):
        self.calls.append(("user.add", kwargs))
        if kwargs["user_id"] in self.users:
            raise ApiError(status_code=409, body="already exists")
        self.users.add(kwargs["user_id"])
        return SimpleNamespace(user_id=kwargs["user_id"])

    async def _user_update(self, user_id, **kwargs):
        self.calls.append(("user.update", {"user_id": user_id, **kwargs}))
        return SimpleNamespace(user_id=user_id)

    async def _thread_create(self, *, thread_id, user_id):
        self.calls.append(("thread.create", {"thread_id": thread_id, "user_id": user_id}))
        if thread_id in self.threads:
            raise ApiError(status_code=409, body="already exists")
        self.threads.add(thread_id)
        return SimpleNamespace(thread_id=thread_id, user_id=user_id)


@pytest.fixture
def fake_zep(monkeypatch) -> FakeZep:
    fake = FakeZep()
    for module in ("app.zep.users", "app.zep.threads", "app.zep.facts", "app.zep.knowledge"):
        monkeypatch.setattr(f"{module}.get_zep", lambda: fake)
    return fake


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest_asyncio.fixture(scope="session")
async def mongo_store() -> MongoStore:
    """Uma conexao por sessao com o banco descartavel; apagado no fim."""
    get_settings.cache_clear()
    s = create_store(get_settings(), database=TEST_DB)
    await s.open()
    yield s
    assert s._client is not None
    await s._client.drop_database(TEST_DB)
    await s.close()


@pytest_asyncio.fixture(autouse=True)
async def clean_db(mongo_store) -> None:
    """Cada teste comeca com as colecoes vazias (o banco e compartilhado na sessao)."""
    db = mongo_store._client[TEST_DB]
    for name in COLLECTIONS:
        await db[name].delete_many({})


@pytest_asyncio.fixture
async def store(mongo_store) -> MongoStore:
    return mongo_store


@pytest_asyncio.fixture
async def members(store, settings) -> MemberDirectory:
    d = MemberDirectory(store, settings)
    await d.load()
    await store.set_member_names({"m1": "Rafael Torres"})
    await d.load()
    return d
