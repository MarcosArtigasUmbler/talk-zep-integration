"""Reenvia webhooks gravados (JSONL, um evento por linha) para a API local.

Serve para testar a integracao com payloads reais sem depender do Talk, e
para reprocessar um lote de eventos exportado de outro ambiente. Como o
EventId e preservado, eventos ja processados sao ignorados.

Uso:
    python -m scripts.replay_events eventos.jsonl
    python -m scripts.replay_events eventos.jsonl --url http://localhost:8000 --token abc
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()


def read_events(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


async def main(bodies: list[dict], url: str, token: str, pause: float) -> int:
    sent = dup = err = 0
    async with httpx.AsyncClient(timeout=10) as client:
        for body in bodies:
            resp = await client.post(
                f"{url.rstrip('/')}/webhooks/talk", params={"token": token}, json=body
            )
            if resp.status_code >= 300:
                err += 1
                print(f"  {resp.status_code}: {resp.text[:200]}")
            elif resp.json().get("duplicate"):
                dup += 1
            else:
                sent += 1
            if pause:
                await asyncio.sleep(pause)
    print(f"enviados {sent}, duplicados {dup}, erros {err}")
    return 1 if err else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--url", default=os.getenv("REPLAY_URL", "http://localhost:8000"))
    parser.add_argument("--token", default=os.getenv("WEBHOOK_TOKEN", ""))
    parser.add_argument("--pause", type=float, default=0.0, help="segundos entre eventos")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(read_events(args.path), args.url, args.token, args.pause)))
