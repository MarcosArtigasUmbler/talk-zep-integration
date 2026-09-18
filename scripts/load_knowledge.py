"""Carrega os arquivos de ``knowledge/`` no grafo da Umbler.

Cada ``.md`` ou ``.txt`` vira um ou mais episodios de texto (quebrados por
paragrafo quando passam de 10.000 caracteres). O nome do arquivo vira a
``source_description``. Um cabecalho opcional::

    ---
    source: Politica comercial 2026
    created_at: 2026-01-01T00:00:00Z
    ---

sobrescreve a descricao e a data em que o conteudo passou a valer.

Uso:
    python -m scripts.load_knowledge            # tudo em knowledge/
    python -m scripts.load_knowledge docs/*.md  # arquivos especificos
    python -m scripts.load_knowledge --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import os
import sys

from dotenv import load_dotenv

load_dotenv()

from zep_cloud.core.api_error import ApiError

from app.zep.client import close_zep
from app.zep.knowledge import add_knowledge_batch, chunk_text, ensure_org_graph


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, text[end + 4 :].lstrip()


def load_files(paths: list[str]) -> list[tuple[str, str | None, str | None]]:
    items = []
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            meta, body = parse_front_matter(fh.read())
        if not body.strip():
            continue
        source = meta.get("source") or os.path.splitext(os.path.basename(path))[0].replace("-", " ")
        items.append((body, f"Base de conhecimento Umbler — {source}", meta.get("created_at")))
    return items


async def main(paths: list[str], dry_run: bool) -> int:
    if not paths:
        paths = sorted(glob.glob("knowledge/*.md") + glob.glob("knowledge/*.txt"))
    # Arquivos com prefixo "_" sao modelos/rascunhos, nunca vao para o grafo.
    paths = [p for p in paths if not os.path.basename(p).startswith("_")]
    if not paths:
        print("nenhum arquivo encontrado", file=sys.stderr)
        return 1
    items = load_files(paths)
    total_chunks = sum(len(chunk_text(t)) for t, _, _ in items)
    print(f"{len(items)} arquivo(s), {total_chunks} episodio(s)")
    for text, source, created_at in items:
        print(
            f"  {source:<60} {len(text):>6} chars  {len(chunk_text(text))} parte(s)  {created_at or ''}"
        )
    if dry_run:
        return 0
    try:
        await ensure_org_graph()
        episodes = await add_knowledge_batch(items)
        print(f"enviados {len(episodes)} episodio(s); a extracao segue em segundo plano (minutos)")
        return 0
    except ApiError as exc:
        print(f"Erro da API do Zep {exc.status_code}: {exc.body}", file=sys.stderr)
        return 1
    finally:
        await close_zep()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.paths, args.dry_run)))
