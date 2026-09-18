"""Prepara o projeto do Zep. Rode ANTES do primeiro webhook.

Ordem recomendada pela documentacao (e a que este script segue):

    1. criar o grafo da Umbler (graph_id)
    2. aplicar a ontologia            -- nao e retroativa
    3. aplicar as instrucoes de idioma/dominio

Uso:
    python -m scripts.setup_zep
    python -m scripts.setup_zep --skip-instructions
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from zep_cloud.core.api_error import ApiError

from app.config import get_settings
from app.zep.client import close_zep, get_zep
from app.zep.instructions import apply_instructions
from app.zep.knowledge import ensure_org_graph
from app.zep.ontology import apply_ontology, verify_ontology


async def main(skip_ontology: bool, skip_instructions: bool) -> int:
    settings = get_settings()
    try:
        created = await ensure_org_graph()
        print(
            f"1. grafo da Umbler {'criado' if created else 'ja existia'}: {settings.zep_org_graph_id}"
        )

        if skip_ontology:
            print("2. ontologia: pulada")
        else:
            await apply_ontology()
            ont = await get_zep().graph.list_entity_types()
            print("2. ontologia aplicada")
            print("   entidades:", [e.name for e in (ont.entity_types or [])])
            print("   arestas  :", [e.name for e in (ont.edge_types or [])])
        diff = await verify_ontology()
        if not diff.matches:
            print("   AVISO: divergencia entre codigo e Zep:", diff)

        if skip_instructions:
            print("3. instrucoes: puladas")
        else:
            removed = await apply_instructions()
            print("3. instrucoes aplicadas ao projeto inteiro (removidas antes:", removed, ")")
        return 0
    except ApiError as exc:
        print(f"Erro da API do Zep {exc.status_code}: {exc.body}", file=sys.stderr)
        return 1
    finally:
        await close_zep()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-ontology", action="store_true")
    parser.add_argument("--skip-instructions", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.skip_ontology, args.skip_instructions)))
