"""Fila em memoria + diario no Store (MongoDB em producao).

O webhook do Talk exige resposta em menos de 5 segundos; o Zep pode levar bem
mais. Entao o handler HTTP so grava o evento e enfileira; estes workers fazem
o resto, com retentativa exponencial. Na subida, tudo que ficou pendente no
diario volta para a fila.
"""

from __future__ import annotations

import asyncio
import logging
import random

from pydantic import ValidationError
from zep_cloud.core.api_error import ApiError

from app.config import Settings
from app.pipeline.handlers import EventHandler
from app.pipeline.store import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    STATUS_RETRY,
    STATUS_SKIPPED,
    Store,
)
from app.talk.members import MemberDirectory
from app.talk.models import WebhookEvent

log = logging.getLogger(__name__)

BASE_BACKOFF_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 300.0


class Worker:
    def __init__(self, store: Store, settings: Settings, members: MemberDirectory) -> None:
        self.store = store
        self.settings = settings
        self.handler = EventHandler(store, settings, members)
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._retry_tasks: set[asyncio.Task] = set()
        self._stopping = False

    async def start(self) -> None:
        pending = await self.store.pending_event_ids()
        for event_id in pending:
            self.queue.put_nowait(event_id)
        if pending:
            log.info("fila: %d evento(s) pendentes recuperados do diario", len(pending))
        for i in range(max(1, self.settings.workers)):
            self._tasks.append(asyncio.create_task(self._run(i), name=f"talk-zep-worker-{i}"))

    async def stop(self) -> None:
        self._stopping = True
        for task in list(self._retry_tasks):
            task.cancel()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, *self._retry_tasks, return_exceptions=True)
        self._tasks.clear()

    def enqueue(self, event_id: str) -> None:
        self.queue.put_nowait(event_id)

    @property
    def size(self) -> int:
        return self.queue.qsize()

    async def _run(self, index: int) -> None:
        while not self._stopping:
            event_id = await self.queue.get()
            try:
                await self.process(event_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("worker %d: erro inesperado em %s", index, event_id)
            finally:
                self.queue.task_done()

    async def process(self, event_id: str) -> None:
        row = await self.store.get_event(event_id)
        if row is None:
            log.warning("evento %s sumiu do diario", event_id)
            return
        if row["status"] in (STATUS_DONE, STATUS_SKIPPED):
            return
        await self.store.mark(event_id, STATUS_PROCESSING, bump_attempts=True)
        attempts = int(row["attempts"]) + 1

        try:
            event = WebhookEvent.parse(row["payload"])
        except (ValidationError, ValueError) as exc:
            await self.store.mark(event_id, STATUS_FAILED, error=f"payload invalido: {exc}"[:2000])
            log.error("evento %s com payload invalido: %s", event_id, exc)
            return

        try:
            outcome = await self.handler.handle(event, event_id=event_id)
        except ApiError as exc:
            await self._on_failure(event_id, attempts, f"zep {exc.status_code}: {exc.body}")
            return
        except Exception as exc:  # noqa: BLE001
            await self._on_failure(event_id, attempts, f"{type(exc).__name__}: {exc}")
            return

        status = STATUS_SKIPPED if outcome.status == "skipped" else STATUS_DONE
        await self.store.mark(event_id, status, summary=outcome.summary)
        log.info("evento %s [%s] %s -> %s", event_id, event.type, status, outcome.summary)

    async def _on_failure(self, event_id: str, attempts: int, error: str) -> None:
        error = error[:2000]
        if attempts >= self.settings.max_attempts:
            await self.store.mark(event_id, STATUS_FAILED, error=error)
            log.error(
                "evento %s falhou definitivamente apos %d tentativas: %s", event_id, attempts, error
            )
            return
        delay = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)))
        delay += random.uniform(0, delay / 4)
        await self.store.mark(event_id, STATUS_RETRY, error=error)
        log.warning(
            "evento %s tentativa %d falhou (%s); retry em %.0fs", event_id, attempts, error, delay
        )
        task = asyncio.create_task(self._requeue_later(event_id, delay))
        self._retry_tasks.add(task)
        task.add_done_callback(self._retry_tasks.discard)

    async def _requeue_later(self, event_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if not self._stopping:
            self.enqueue(event_id)
