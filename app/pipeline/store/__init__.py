"""Persistencia local: MongoDB em producao, SQLite em desenvolvimento e testes."""

from __future__ import annotations

from app.config import Settings
from app.pipeline.store.base import (
    RESUMABLE_STATUSES,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    STATUS_RETRY,
    STATUS_SKIPPED,
    Store,
    fact_key,
    now_iso,
)
from app.pipeline.store.sqlite import SQLiteStore


def create_store(settings: Settings) -> Store:
    """Escolhe o backend: ``STORE_BACKEND`` ou, em ``auto``, Mongo se houver URI."""
    backend = settings.store_backend
    if backend == "auto":
        backend = "mongodb" if settings.mongodb_uri else "sqlite"
    if backend == "mongodb":
        if not settings.mongodb_uri:
            raise ValueError("STORE_BACKEND=mongodb exige MONGODB_URI")
        from app.pipeline.store.mongo import MongoStore

        return MongoStore(settings.mongodb_uri, settings.mongodb_db)
    return SQLiteStore(settings.database_path)


__all__ = [
    "RESUMABLE_STATUSES",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_PROCESSING",
    "STATUS_RETRY",
    "STATUS_SKIPPED",
    "SQLiteStore",
    "Store",
    "create_store",
    "fact_key",
    "now_iso",
]
