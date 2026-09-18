"""POST /webhooks/talk -- o unico ponto de contato com o Umbler Talk.

Contrato do Talk: status 2xx em menos de 5 segundos, corpo ignorado, ate 2
re-tentativas com o mesmo ``EventId`` (header ``x-attempt``). Aqui so se grava
e enfileira; nada de Zep no caminho da resposta.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from app.schemas import WebhookAck
from app.talk.models import peek_envelope

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhook"])


def _authorized(request: Request, token_query: str | None, token_header: str | None) -> bool:
    expected = request.app.state.settings.webhook_token
    if not expected:
        return True
    supplied = token_header or token_query or ""
    return hmac.compare_digest(supplied, expected)


@router.post("/webhooks/talk", response_model=WebhookAck, status_code=status.HTTP_202_ACCEPTED)
async def receive_talk_webhook(
    request: Request,
    response: Response,
    token: str | None = Query(default=None),
    x_webhook_token: str | None = Header(default=None),
    x_attempt: str | None = Header(default=None),
) -> WebhookAck:
    if not _authorized(request, token, x_webhook_token):
        raise HTTPException(status_code=401, detail="token invalido")

    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="corpo nao e JSON") from exc

    event_id, event_type, event_date = peek_envelope(body)
    if not event_id:
        raise HTTPException(status_code=400, detail="EventId ausente")

    store = request.app.state.store
    is_new = await store.record_event(event_id, event_type, event_date, body)
    if is_new:
        worker = getattr(request.app.state, "worker", None)
        if worker is not None:
            worker.enqueue(event_id)
        log.info("webhook %s [%s] recebido (tentativa %s)", event_id, event_type, x_attempt or "1")
    else:
        response.status_code = status.HTTP_200_OK
        log.info("webhook %s reentregue (tentativa %s); ignorado", event_id, x_attempt or "?")
    return WebhookAck(accepted=True, duplicate=not is_new, event_id=event_id)
