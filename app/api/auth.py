"""Chave de API para as rotas de leitura e administracao.

O webhook do Talk tem o proprio token (``WEBHOOK_TOKEN``). Todas as outras
rotas expoem dados de clientes ou controlam a fila, e a API fica publica atras
do nginx sem autenticacao basica; por isso exigem ``X-API-Key`` igual a
``API_KEY``. Com ``API_KEY`` vazio a verificacao fica desligada (so em
desenvolvimento) e a aplicacao avisa na subida.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(request: Request, api_key: str | None = Security(api_key_header)) -> None:
    expected = request.app.state.settings.api_key
    if not expected:
        return
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key ausente ou invalida",
            headers={"WWW-Authenticate": "ApiKey"},
        )
