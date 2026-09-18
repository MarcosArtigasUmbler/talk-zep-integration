"""Persistencia local da integracao: MongoDB, em todos os ambientes."""

from __future__ import annotations

from urllib.parse import quote_plus

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
from app.pipeline.store.mongo import MongoStore


def mongodb_uri_with_credentials(uri: str, username: str, password: str) -> str:
    """Injeta usuario/senha na URI quando ela vem sem credenciais.

    ``mongodb+srv://host/...`` + MONGODB_USERNAME/PASSWORD -> ``mongodb+srv://u:p@host/...``.
    Se a URI ja tem ``@`` (credenciais embutidas) ou nao ha usuario, volta intacta.
    """
    if not username or "://" not in uri:
        return uri
    scheme, rest = uri.split("://", 1)
    if "@" in rest.split("/", 1)[0]:
        return uri
    return f"{scheme}://{quote_plus(username)}:{quote_plus(password)}@{rest}"


def create_store(settings: Settings, *, database: str | None = None) -> MongoStore:
    uri = mongodb_uri_with_credentials(
        settings.mongodb_uri, settings.mongodb_username, settings.mongodb_password
    )
    return MongoStore(uri, database or settings.mongodb_db)


__all__ = [
    "RESUMABLE_STATUSES",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_PROCESSING",
    "STATUS_RETRY",
    "STATUS_SKIPPED",
    "MongoStore",
    "Store",
    "create_store",
    "fact_key",
    "mongodb_uri_with_credentials",
    "now_iso",
]
