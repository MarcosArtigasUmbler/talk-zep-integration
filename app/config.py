"""Configuracao da aplicacao, lida do ambiente / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Zep -----------------------------------------------------------------
    zep_api_key: str
    zep_org_graph_id: str = "umbler_conhecimento"
    """Grafo avulso (graph_id) com o conhecimento da Umbler. Nunca dado de cliente."""
    zep_strict_ontology: bool = False

    # --- Autenticacao --------------------------------------------------------
    webhook_token: str = ""
    """Token do webhook do Talk (?token= ou X-Webhook-Token). Vazio desliga (so dev)."""
    api_key: str = ""
    """X-API-Key das rotas de leitura/admin. Vazio desliga a verificacao (so dev)."""
    ingest_group_chats: bool = False
    ingest_private_notes: bool = True

    # --- Persistencia (MongoDB em todos os ambientes) ------------------------
    mongodb_uri: str
    mongodb_username: str = ""
    mongodb_password: str = ""
    """Opcionais: usados so se a URI nao trouxer credenciais embutidas."""
    mongodb_db: str = "talk_zep"

    # --- Fila ----------------------------------------------------------------
    workers: int = 4
    max_attempts: int = 5
    start_worker: bool = True
    """Desligado nos testes para inspecionar a fila sem processar."""

    # --- Talk API (somente leitura, opcional) --------------------------------
    talk_api_base: str = "https://app-utalk.umbler.com/api"
    talk_api_token: str = ""
    talk_organization_id: str = ""
    members_file: str = "members.json"

    log_level: str = "INFO"
    app_name: str = "talk-zep-integration"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
