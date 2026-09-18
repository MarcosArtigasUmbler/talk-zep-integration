"""Mostra o que o Zep sabe sobre um contato do Talk (ou sobre a Umbler).

Uso:
    python -m scripts.show_contact                    # lista contatos conhecidos
    python -m scripts.show_contact <talk_contact_id>  # contexto, nos e fatos
    python -m scripts.show_contact --umbler           # grafo da empresa
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from dotenv import load_dotenv

load_dotenv()

from app.config import get_settings
from app.pipeline.store import Store
from app.talk.normalize import contact_user_id
from app.zep import retrieval
from app.zep.client import close_zep, get_zep


def title(text: str) -> None:
    print(f"\n{'=' * 74}\n{text}\n{'=' * 74}")


async def list_contacts(store: Store) -> None:
    rows = await store.list_contacts(200)
    print(f"{len(rows)} contato(s) conhecidos:\n")
    for r in rows:
        print(f"  {r['contact_id']:<24} {r['name'] or '':<30} {r['phone'] or ''}")
    print("\nUse: python -m scripts.show_contact <contact_id>")


async def show_contact(store: Store, contact_id: str) -> None:
    user_id = contact_user_id(contact_id)
    thread_id = await store.latest_thread_for_user(user_id)
    title(f"CONTATO {contact_id} -> {user_id} (thread {thread_id})")
    if thread_id:
        ctx = await retrieval.get_context(thread_id)
        title("BLOCO DE CONTEXTO")
        print(ctx.strip() or "(vazio — a extração pode ainda estar rodando)")
        title("ÚLTIMAS MENSAGENS")
        for m in await retrieval.get_recent_messages(thread_id, lastn=10):
            print(f"  [{(m['created_at'] or '')[:19]}] {m['role']:<9} {m['name']}: {m['content']}")
    dump = await retrieval.graph_dump(user_id)
    title("NÓS")
    for n in dump["nodes"]:
        print(f"  {n['name']}  [{', '.join(n['labels'] or []) or 'sem rótulo'}]")
    title("FATOS")
    for e in dump["edges"]:
        flag = " (invalidado)" if e["invalid_at"] or e["expired_at"] else ""
        print(f"  [{e['name']}] {e['fact']}{flag}")


async def show_umbler() -> None:
    zep = get_zep()
    gid = get_settings().zep_org_graph_id
    title(f"CONHECIMENTO DA UMBLER — {gid}")
    for n in await zep.graph.node.get_by_graph_id(graph_id=gid) or []:
        print(f"  {n.name}  [{', '.join(n.labels or []) or 'sem rótulo'}]")
    title("FATOS")
    for e in await zep.graph.edge.get_by_graph_id(graph_id=gid) or []:
        print(f"  [{e.name}] {e.fact}")


async def main(contact_id: str | None, umbler: bool) -> None:
    store = Store(get_settings().database_path)
    await store.open()
    try:
        if umbler:
            await show_umbler()
        elif contact_id:
            await show_contact(store, contact_id)
        else:
            await list_contacts(store)
    finally:
        await store.close()
        await close_zep()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contact_id", nargs="?")
    parser.add_argument("--umbler", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.contact_id, args.umbler))
