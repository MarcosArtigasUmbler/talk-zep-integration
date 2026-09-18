"""API que recebe os webhooks do Umbler Talk e alimenta a memoria no Zep.

Fluxo:

    Umbler Talk --webhook--> POST /webhooks/talk --> Store (diario) --> fila
        --> worker --> Zep (usuario, thread, mensagens, fatos)

    Agente / copiloto --> GET /contacts/{id}/briefing, POST /search ...

Regra do projeto: **nunca escrever no Umbler Talk**. A unica integracao com o
Talk e receber o webhook (e, opcionalmente, GETs de leitura nos scripts).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, knowledge, memory, webhook
from app.config import get_settings
from app.pipeline.store import create_store
from app.pipeline.worker import Worker
from app.talk.members import MemberDirectory
from app.zep.client import close_zep
from app.zep.ontology import verify_ontology


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    log = logging.getLogger(__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = create_store(settings)
        await store.open()
        log.info("store: %s", type(store).__name__)
        members = MemberDirectory(store, settings)
        await members.load()
        app.state.settings = settings
        app.state.store = store
        app.state.members = members

        if not settings.webhook_token:
            log.warning("WEBHOOK_TOKEN vazio: qualquer um pode postar em /webhooks/talk")

        worker: Worker | None = None
        if settings.start_worker:
            worker = Worker(store, settings, members)
            await worker.start()
            app.state.worker = worker
            try:
                diff = await verify_ontology()
                if diff.matches:
                    log.info("zep: ontologia confere")
                else:
                    log.warning(
                        "zep: ontologia divergente (rode python -m scripts.setup_zep): %s", diff
                    )
            except Exception as exc:  # noqa: BLE001 - nao impede a subida
                log.warning("zep: nao consegui verificar a ontologia: %s", exc)
        try:
            yield
        finally:
            if worker is not None:
                await worker.stop()
            await store.close()
            await close_zep()

    app = FastAPI(
        title=settings.app_name,
        description=__doc__,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(webhook.router)
    app.include_router(memory.router)
    app.include_router(knowledge.router)
    app.include_router(admin.router)
    return app


app = create_app()
