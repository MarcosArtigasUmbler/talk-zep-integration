# talk-zep-integration

API FastAPI que recebe webhooks do Umbler Talk e alimenta a memoria no Zep.

## Regras invioláveis

- **Nunca escrever no Umbler Talk.** A unica integracao e receber o webhook.
  Os scripts de backfill/nomes de membros fazem apenas GET, e sao opcionais.
- Dado de cliente vai no grafo do contato (`user_id`). O grafo da Umbler
  (`graph_id`) so recebe conhecimento geral da empresa.
- Ontologia e instrucoes nao sao retroativas: mudou, rode `scripts/setup_zep`
  e considere reprocessar.

## Comandos

```bash
.venv/Scripts/activate
pip install -r requirements-dev.txt
pytest -q
ruff check .
uvicorn app.main:app --reload
python -m scripts.setup_zep
```

## Onde esta cada coisa

- `app/talk/` -- modelos do webhook (case-insensitive) e normalizacao
- `app/zep/` -- camada do Zep: usuarios, threads, fatos, conhecimento, leitura, ontologia
- `app/pipeline/` -- SQLite (idempotencia/fila) + worker + handlers (evento -> Zep)
- `app/api/` -- rotas HTTP
- `scripts/` -- setup do Zep, carga de conhecimento, backfill (GET only), depuracao
- `APRENDIZADOS-ZEP.md` (em `../getzap-example`) -- base empirica das decisoes

Testes rodam sem rede e sem Zep (fake em `tests/conftest.py`).
