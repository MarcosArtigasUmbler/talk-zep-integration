"""Resolve o nome dos atendentes.

O webhook traz apenas ``OrganizationMember.Id``. O nome vem, nesta ordem:

1. cache local (tabela ``members``);
2. arquivo ``MEMBERS_FILE`` (``{"<memberId>": "Nome"}``), mantido a mao;
3. API do Talk, **somente GET** ``/v1/members/online/`` -- so quando ha token.

Nunca ha escrita no Talk aqui.
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from app.config import Settings
from app.pipeline.store import Store

log = logging.getLogger(__name__)

API_REFRESH_SECONDS = 600


class MemberDirectory:
    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._cache: dict[str, str] = {}
        self._last_api_refresh = 0.0

    async def load(self) -> None:
        self._cache = await self.store.all_member_names()
        from_file = self._read_file()
        if from_file:
            self._cache.update(from_file)
            await self.store.set_member_names(from_file)
        log.info("membros conhecidos: %d", len(self._cache))

    def _read_file(self) -> dict[str, str]:
        path = self.settings.members_file
        if not path or not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            return {str(k): str(v) for k, v in data.items() if v}
        except (OSError, ValueError) as exc:
            log.warning("nao consegui ler %s: %s", path, exc)
            return {}

    async def refresh_from_api(self, force: bool = False) -> int:
        """GET /v1/members/online/ (leitura). Devolve quantos nomes aprendeu."""
        s = self.settings
        if not (s.talk_api_token and s.talk_organization_id):
            return 0
        if not force and time.monotonic() - self._last_api_refresh < API_REFRESH_SECONDS:
            return 0
        self._last_api_refresh = time.monotonic()
        url = f"{s.talk_api_base.rstrip('/')}/v1/members/online/"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    url,
                    params={"organizationId": s.talk_organization_id},
                    headers={"Authorization": f"Bearer {s.talk_api_token}"},
                )
                resp.raise_for_status()
                members = resp.json() or []
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("talk api members/online falhou: %s", exc)
            return 0
        learned: dict[str, str] = {}
        for m in members:
            if not isinstance(m, dict):
                continue
            mid = m.get("id") or m.get("Id")
            name = m.get("displayName") or m.get("DisplayName") or m.get("name")
            if mid and name:
                learned[str(mid)] = str(name)
        if learned:
            self._cache.update(learned)
            await self.store.set_member_names(learned)
        return len(learned)

    async def name_for(self, member_id: str | None) -> str | None:
        if not member_id:
            return None
        name = self._cache.get(member_id)
        if name:
            return name
        if await self.refresh_from_api():
            name = self._cache.get(member_id)
        return name

    @staticmethod
    def label_for(member_id: str | None, name: str | None) -> str:
        """Rotulo estavel para o no do atendente quando nao ha nome."""
        if name:
            return name
        if member_id:
            return f"Atendente {member_id[:8]}"
        return "Atendente Umbler"
