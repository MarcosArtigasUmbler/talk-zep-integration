"""Store em MongoDB -- producao.

Uma colecao por papel, com o identificador natural em ``_id``:

    events(_id=event_id)      messages(_id=message_id)   facts(_id=fact_key)
    contacts(_id=user_id)     chats(_id=chat_id)         members(_id=member_id)

Datas sao guardadas como string ISO 8601 (UTC), iguais as que a API devolve.
Os dicts retornados tem os mesmos campos da implementacao em SQLite.
"""

from __future__ import annotations

from typing import Any

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, UpdateOne
from pymongo.errors import DuplicateKeyError

from app.pipeline.store.base import (
    EVENT_SUMMARY_FIELDS,
    RESUMABLE_STATUSES,
    STATUS_PENDING,
    Store,
    now_iso,
)


def _rename_id(doc: dict | None, field: str) -> dict | None:
    if doc is None:
        return None
    out = dict(doc)
    out[field] = out.pop("_id")
    return out


class MongoStore(Store):
    def __init__(self, uri: str, database: str) -> None:
        self.uri = uri
        self.database = database
        self._client: AsyncMongoClient | None = None

    async def open(self) -> None:
        self._client = AsyncMongoClient(self.uri, serverSelectionTimeoutMS=10_000)
        db = self._client[self.database]
        await db.command("ping")
        self.events = db["events"]
        self.messages = db["messages"]
        self.facts = db["facts"]
        self.contacts = db["contacts"]
        self.chats = db["chats"]
        self.members = db["members"]
        await self.events.create_index([("status", ASCENDING), ("received_at", ASCENDING)])
        await self.events.create_index([("received_at", DESCENDING)])
        await self.chats.create_index([("user_id", ASCENDING), ("last_event_at", DESCENDING)])
        await self.contacts.create_index([("updated_at", DESCENDING)])

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # --- eventos -------------------------------------------------------------

    async def record_event(
        self, event_id: str, event_type: str | None, event_date: str | None, payload: Any
    ) -> bool:
        try:
            await self.events.insert_one(
                {
                    "_id": event_id,
                    "type": event_type,
                    "event_date": event_date,
                    "received_at": now_iso(),
                    "status": STATUS_PENDING,
                    "attempts": 0,
                    "last_error": None,
                    "summary": None,
                    "payload": payload,
                }
            )
            return True
        except DuplicateKeyError:
            return False

    async def get_event(self, event_id: str) -> dict | None:
        return _rename_id(await self.events.find_one({"_id": event_id}), "event_id")

    async def mark(
        self,
        event_id: str,
        status: str,
        *,
        error: str | None = None,
        summary: str | None = None,
        bump_attempts: bool = False,
    ) -> None:
        update: dict[str, Any] = {"$set": {"status": status}}
        if error is not None:
            update["$set"]["last_error"] = error
        if summary is not None:
            update["$set"]["summary"] = summary
        if bump_attempts:
            update["$inc"] = {"attempts": 1}
        await self.events.update_one({"_id": event_id}, update)

    async def reset_event(self, event_id: str) -> bool:
        result = await self.events.update_one(
            {"_id": event_id},
            {"$set": {"status": STATUS_PENDING, "attempts": 0, "last_error": None}},
        )
        return result.matched_count == 1

    async def pending_event_ids(self) -> list[str]:
        cursor = self.events.find({"status": {"$in": list(RESUMABLE_STATUSES)}}, {"_id": 1}).sort(
            "received_at", ASCENDING
        )
        return [doc["_id"] async for doc in cursor]

    async def list_events(self, status: str | None = None, limit: int = 50) -> list[dict]:
        query = {"status": status} if status else {}
        projection = {f: 1 for f in EVENT_SUMMARY_FIELDS if f != "event_id"}
        cursor = self.events.find(query, projection).sort("received_at", DESCENDING).limit(limit)
        return [_rename_id(doc, "event_id") async for doc in cursor]

    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        async for row in await self.events.aggregate(
            [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]
        ):
            out[f"events_{row['_id']}"] = row["n"]
        for name, coll in (
            ("messages", self.messages),
            ("facts", self.facts),
            ("contacts", self.contacts),
            ("chats", self.chats),
        ):
            out[name] = await coll.estimated_document_count()
        return out

    # --- mensagens -----------------------------------------------------------

    async def message_seen(self, message_id: str) -> bool:
        return await self.messages.find_one({"_id": message_id}, {"_id": 1}) is not None

    async def mark_messages(self, items: list[tuple[str, str | None, str]]) -> None:
        if not items:
            return
        now = now_iso()
        ops = [
            UpdateOne(
                {"_id": m},
                {"$setOnInsert": {"event_id": e, "thread_id": t, "created_at": now}},
                upsert=True,
            )
            for m, e, t in items
        ]
        await self.messages.bulk_write(ops, ordered=False)

    # --- fatos ---------------------------------------------------------------

    async def fact_seen(self, key: str) -> bool:
        return await self.facts.find_one({"_id": key}, {"_id": 1}) is not None

    async def mark_fact(self, key: str, user_id: str, fact_name: str, fact: str) -> None:
        await self.facts.update_one(
            {"_id": key},
            {
                "$setOnInsert": {
                    "user_id": user_id,
                    "fact_name": fact_name,
                    "fact": fact,
                    "created_at": now_iso(),
                }
            },
            upsert=True,
        )

    # --- contatos ------------------------------------------------------------

    async def contact_fingerprint(self, user_id: str) -> str | None:
        doc = await self.contacts.find_one({"_id": user_id}, {"fingerprint": 1})
        return doc["fingerprint"] if doc else None

    async def upsert_contact(
        self,
        user_id: str,
        contact_id: str,
        name: str | None,
        phone: str | None,
        fingerprint: str,
        tags: list[str] | None = None,
    ) -> None:
        await self.contacts.update_one(
            {"_id": user_id},
            {
                "$set": {
                    "contact_id": contact_id,
                    "name": name,
                    "phone": phone,
                    "fingerprint": fingerprint,
                    "tags": list(tags or []),
                    "updated_at": now_iso(),
                }
            },
            upsert=True,
        )

    async def get_contact(self, user_id: str) -> dict | None:
        return _rename_id(await self.contacts.find_one({"_id": user_id}), "user_id")

    async def list_contacts(self, limit: int = 100) -> list[dict]:
        cursor = self.contacts.find({}).sort("updated_at", DESCENDING).limit(limit)
        return [_rename_id(doc, "user_id") async for doc in cursor]

    # --- chats ---------------------------------------------------------------

    async def chat_known(self, chat_id: str) -> bool:
        return await self.chats.find_one({"_id": chat_id}, {"_id": 1}) is not None

    async def upsert_chat(
        self,
        chat_id: str,
        user_id: str,
        thread_id: str,
        *,
        created_at: str | None,
        last_event_at: str | None,
        open_: bool | None,
        sector: str | None,
        member_id: str | None,
    ) -> None:
        # None nao sobrescreve (equivalente ao COALESCE do SQLite): vai so em
        # $setOnInsert, para o documento nascer com todos os campos.
        set_fields: dict[str, Any] = {
            "user_id": user_id,
            "thread_id": thread_id,
            "last_event_at": last_event_at or now_iso(),
        }
        insert_only: dict[str, Any] = {}
        for key, value in (
            ("created_at", created_at),
            ("open", open_),
            ("sector", sector),
            ("member_id", member_id),
        ):
            if value is not None:
                set_fields[key] = value
            else:
                insert_only[key] = None
        update: dict[str, Any] = {"$set": set_fields}
        if insert_only:
            update["$setOnInsert"] = insert_only
        await self.chats.update_one({"_id": chat_id}, update, upsert=True)

    async def latest_thread_for_user(self, user_id: str) -> str | None:
        doc = await self.chats.find_one(
            {"user_id": user_id}, {"thread_id": 1}, sort=[("last_event_at", DESCENDING)]
        )
        return doc["thread_id"] if doc else None

    async def threads_for_user(self, user_id: str) -> list[dict]:
        cursor = self.chats.find({"user_id": user_id}, {"user_id": 0}).sort(
            "last_event_at", DESCENDING
        )
        return [_rename_id(doc, "chat_id") async for doc in cursor]

    # --- membros -------------------------------------------------------------

    async def member_name(self, member_id: str) -> str | None:
        doc = await self.members.find_one({"_id": member_id}, {"name": 1})
        return doc["name"] if doc else None

    async def set_member_names(self, names: dict[str, str]) -> None:
        if not names:
            return
        now = now_iso()
        ops = [
            UpdateOne({"_id": k}, {"$set": {"name": v, "updated_at": now}}, upsert=True)
            for k, v in names.items()
        ]
        await self.members.bulk_write(ops, ordered=False)

    async def all_member_names(self) -> dict[str, str]:
        return {doc["_id"]: doc["name"] async for doc in self.members.find({}, {"name": 1})}
