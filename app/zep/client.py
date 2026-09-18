"""Cliente AsyncZep unico por processo (ele guarda um pool HTTP)."""

from __future__ import annotations

from functools import lru_cache

from zep_cloud.client import AsyncZep

from app.config import get_settings


@lru_cache
def get_zep() -> AsyncZep:
    return AsyncZep(api_key=get_settings().zep_api_key)


async def close_zep() -> None:
    """Fecha o pool HTTP. O httpx real fica dois niveis abaixo do wrapper do SDK."""
    wrapper = getattr(get_zep(), "_client_wrapper", None)
    http_client = getattr(wrapper, "httpx_client", None)
    inner = getattr(http_client, "httpx_client", http_client)
    if inner is not None and hasattr(inner, "aclose"):
        await inner.aclose()
    get_zep.cache_clear()
