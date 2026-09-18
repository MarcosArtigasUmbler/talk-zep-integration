# talk-zep-integration

API em Python (FastAPI) que recebe os **webhooks do Umbler Talk** e alimenta a
memória de um agente de IA no **Zep**: um grafo por contato com todas as
conversas do time de vendas, a ficha do contato como fatos estruturados e um
grafo separado com o conhecimento geral da Umbler.

> **Regra do projeto: nada é alterado no Umbler Talk.** A única integração é
> receber o webhook. Os scripts opcionais de backfill e de nomes de atendentes
> fazem apenas `GET` na API do Talk.

```
Umbler Talk ──webhook──▶ POST /webhooks/talk ──▶ SQLite (diário) ──▶ fila ──▶ workers ──▶ Zep
                                                                                   │
                     agente de IA / copiloto ◀── GET /contacts/{id}/briefing ◀──────┘
```

## Como o Talk vira memória

| No Talk | No Zep | Por quê |
|---|---|---|
| Contato (`Contact.Id`) | **User** `talk_contact_<id>` | O usuário do Zep é o cliente, não o vendedor. O histórico sobrevive à troca de vendedor. ID estável; telefone muda. |
| Chat (`Chat.Id`) | **Thread** `talk_chat_<id>` | Todas as threads de um contato alimentam o mesmo grafo. |
| Mensagem do contato | `Message(role="user")` | Material para o Zep interpretar. |
| Mensagem de membro / bot | `Message(role="assistant", name=<atendente>)` | Idem. |
| Nota interna (`IsPrivate`) | Episódio de texto no grafo do contato | Não é conversa; é observação do time sobre o cliente. |
| Setor, atendente, tags, canal, encerramento | **`graph.add_fact_triple`** | Dado que já chega estruturado não passa pelo LLM: elimina erros de extração. |
| Conhecimento da Umbler | Grafo avulso `ZEP_ORG_GRAPH_ID` | Legível pelo agente de qualquer contato. **Nunca dado de cliente.** |

O `created_at` de cada mensagem e o `valid_at` de cada fato usam a data do
evento no Talk, não a do recebimento. É isso que faz o grafo bitemporal do Zep
responder "o que era verdade em março" e invalidar fatos antigos.

### Eventos do webhook

Formato oficial (`help.umbler.com > Como criar Webhooks`): `Type`, `EventDate`,
`EventId` e `Payload.Content` com um `BasicChatModel` da API do Talk. O parser
aceita PascalCase e camelCase.

| Evento | O que a integração faz |
|---|---|
| `Message`, `MessageFileUploaded` | Grava a mensagem na thread (ou a nota interna no grafo). |
| `NewChat` | Cria a thread; fatos de canal, setor e atendente. |
| `MemberTransfer` | Fato `ATENDIDO_POR` com o novo atendente. |
| `ChatSectorChanged` | Fato `ATENDIDO_NO_SETOR`. |
| `ChatTagChanged` | Fatos `TEM_TAG` (tags do contato e da conversa). |
| `ChatClosed` | Fato `ATENDIMENTO_ENCERRADO`. |
| `ChatPrivateStatusChanged` | Ignorado (é visibilidade interna, não memória). |

Independentemente do tipo, todo evento sincroniza a ficha do contato (só se
mudou) e os fatos estruturados (só os novos). Conversas internas entre membros
são ignoradas; grupos ficam fora por padrão (`INGEST_GROUP_CHATS`).

### Contrato do webhook e a fila

O Talk exige **2xx em menos de 5 segundos**, reenvia até 2 vezes com o mesmo
`EventId` (header `x-attempt`) e pausa o webhook após 100 falhas no dia. Por
isso o handler HTTP só grava o evento no SQLite e enfileira; os workers falam
com o Zep depois, com retentativa exponencial (`MAX_ATTEMPTS`). Evento
reentregue é reconhecido pelo `EventId` e ignorado. Na subida, eventos que
ficaram pendentes voltam para a fila.

O Talk não assina o webhook. Cadastre a URL como
`https://seu-host/webhooks/talk?token=<WEBHOOK_TOKEN>` (ou envie o header
`X-Webhook-Token`).

## Setup

```bash
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                                # preencha ZEP_API_KEY e WEBHOOK_TOKEN
```

Prepare o Zep **antes do primeiro webhook** (ontologia e instruções não são
retroativas):

```bash
python -m scripts.setup_zep
```

Isso cria o grafo da Umbler, aplica a ontologia e as instruções de idioma.
Depois suba a API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Documentação interativa em `/docs`. Rode os testes com `pytest -q` (não usam
rede nem o Zep real).

## Endpoints

| Método | Rota | Uso |
|---|---|---|
| `POST` | `/webhooks/talk` | Recebe o webhook do Talk. Único ponto de entrada de dados do Talk. |
| `GET` | `/contacts/{contact_id}/briefing` | **Para o agente/copiloto.** Contexto do grafo + últimas mensagens + fatos da Umbler relevantes, numa chamada. |
| `GET` | `/contacts/{contact_id}/context` | Só o bloco de contexto do Zep (memória durável). |
| `GET` | `/contacts/{contact_id}/recent?n=10` | Só as últimas mensagens literais (memória curta). |
| `GET` | `/contacts/{contact_id}/graph` | Nós e fatos do grafo do contato (depuração). |
| `GET` | `/contacts` | Contatos conhecidos pela integração. |
| `POST` | `/search` | `graph.search` no grafo de um contato ou, sem `contact_id`, no grafo da Umbler. |
| `POST` | `/knowledge`, `/knowledge/batch` | Adiciona conhecimento da Umbler ao grafo avulso. |
| `GET` | `/health`, `/ontology`, `/admin/events` | Saúde, drift da ontologia, diário de eventos. |
| `POST` | `/admin/events/{id}/retry` | Recoloca um evento na fila. |

`contact_id` é o id do contato **no Talk** (o `user_id` do Zep é derivado).

### Duas memórias, sempre

Medido no projeto anterior: uma mensagem gravada aparece em
`thread.get` **na hora**, em `graph.search` em **~6 minutos** e no bloco de
contexto **depois disso**. Um agente alimentado só pelo contexto responde como
quem acabou de entrar na conversa. Por isso o `/briefing` devolve as duas
coisas, e o agente deve usar ambas:

- `recent_messages` — o que acabou de ser dito (literal);
- `context` — quem é essa pessoa, em todas as conversas (destilado);
- `company_knowledge` — fatos do grafo da Umbler relevantes para a última
  mensagem do contato (corte de score em 0,5 com `cross_encoder`).

Coloque esse conteúdo no canal do **usuário** do prompt, não no `system`: foi
escrito por clientes. Se alguém digitar "ignore suas instruções" no WhatsApp,
isso entra no grafo.

## Conhecimento da Umbler

Coloque arquivos `.md`/`.txt` em `knowledge/` (um assunto por arquivo, frases
completas e afirmativas, valores exatos) e rode:

```bash
python -m scripts.load_knowledge --dry-run
python -m scripts.load_knowledge
```

`knowledge/_MODELO.md` explica o formato; arquivos com `_` são ignorados.
Textos acima de 10.000 caracteres são divididos por parágrafo. Use o
cabeçalho `created_at` para dizer desde quando o conteúdo vale (tabela de
preço nova, política nova): o Zep invalida o fato anterior em vez de manter
duas verdades.

## Ontologia

Definida em `app/zep/ontology.py`. O tipo embutido `User` é o contato.

| Entidades | Arestas |
|---|---|
| `Vendedor`, `EmpresaCliente`, `ProdutoUmbler`, `Plano`, `Negociacao`, `Objecao`, `Concorrente`, `Setor`, `Tag`, `Canal` | `ATENDIDO_POR`, `ATENDIDO_NO_SETOR`, `ATENDIMENTO_ENCERRADO`, `TEM_TAG`, `CONTATO_PELO_CANAL`, `TRABALHA_EM`, `INTERESSADO_EM`, `TEM_NEGOCIACAO`, `AVALIA_PLANO`, `LEVANTOU_OBJECAO`, `COMPARA_COM` |

Os cinco primeiros tipos de aresta são escritos por nós via `fact_triple`; os
demais são dicas para o extrator sobre o que procurar nas conversas. A
quantidade máxima de tipos depende do plano do Zep; se a API recusar, remova
tipos e rode o setup de novo. `GET /ontology` mostra o drift entre código e
Zep; a API loga um aviso na subida.

`ZEP_STRICT_ONTOLOGY=true` restringe a extração aos tipos acima. Reduz ruído
(`SAUDOU`, `GREETS`...), mas descarta o que não se encaixa em nenhum tipo. Meça
antes de ligar em produção.

## Nome dos atendentes

O webhook traz só o `Id` do membro. O nome é resolvido, nesta ordem, pelo
cache local, pelo arquivo `MEMBERS_FILE` (`members.json`, veja
`members.json.example`) e, se `TALK_API_TOKEN` estiver configurado, por
`GET /v1/members/online/`. Sem nome, o atendente entra como `Atendente <id>`.

## Backfill do histórico (opcional, somente leitura)

O webhook só entrega o que acontece a partir do cadastro. Para trazer o
histórico anterior, `scripts/backfill_talk.py` lê chats e mensagens com `GET`
e grava no Zep em lote (`add_messages_batch`, muito mais rápido que unitário)
em ordem cronológica e com as datas reais:

```bash
python -m scripts.backfill_talk --days 90 --state All --dry-run
python -m scripts.backfill_talk --days 90 --state All
```

Requer `TALK_API_TOKEN` e `TALK_ORGANIZATION_ID`. Mensagens já enviadas são
reconhecidas pelo id e não duplicam com o que chega pelo webhook.

## Outros scripts

```bash
python -m scripts.show_contact                # contatos conhecidos
python -m scripts.show_contact <contact_id>   # contexto, mensagens, nós e fatos
python -m scripts.show_contact --umbler       # grafo da empresa
python -m scripts.replay_events eventos.jsonl # reenvia webhooks gravados (1 por linha)
```

## Layout

```
app/
  main.py               FastAPI + lifespan (SQLite, worker, checagem da ontologia)
  config.py             settings (.env)
  schemas.py            modelos HTTP
  talk/models.py        webhook do Talk (case-insensitive, campos do BasicChatModel)
  talk/normalize.py     Talk -> Zep: ids, papéis, texto de cada tipo de mensagem
  talk/members.py       nome dos atendentes (cache, arquivo, GET opcional)
  pipeline/store.py     SQLite: diário/idempotência, mensagens, fatos, contatos, chats
  pipeline/worker.py    fila + retentativa
  pipeline/handlers.py  evento -> escritas no Zep
  zep/users.py          user.add / update
  zep/threads.py        thread.create / add_messages / add_messages_batch
  zep/facts.py          graph.add_fact_triple e as fábricas de fatos
  zep/knowledge.py      grafo da Umbler (chunking, batch) e notas internas
  zep/retrieval.py      contexto, mensagens recentes, graph.search
  zep/ontology.py       tipos de entidade/aresta + drift check
  zep/instructions.py   instruções de idioma/domínio
scripts/                setup_zep, load_knowledge, backfill_talk, replay_events, show_contact
tests/                  sem rede; Zep falso em conftest.py
knowledge/              arquivos de conhecimento da Umbler
```

## Limitações conhecidas

- **Latência do Zep.** Extração assíncrona: fatos e contexto levam minutos.
  Nunca espere a ingestão dentro do webhook.
- **Mudança de setor/atendente.** Enviamos o fato novo com `valid_at`; a
  invalidação do anterior fica a cargo do Zep (contradição). O SQLite guarda o
  último estado por chat, então reenvios não duplicam.
- **Idioma do resumo** do usuário é inconsistente mesmo com instrução (medido).
  Os fatos obedecem; se o idioma do resumo for crítico, valide na sua camada.
- **Mídia.** Imagem, vídeo e arquivo entram como `[imagem] legenda`,
  `[arquivo: nome]` etc. Áudio entra com a transcrição do Talk quando houver.
- **Schema do webhook.** Os campos vêm do swagger da API do Talk
  (`app-utalk.umbler.com/api/docs`). Campos desconhecidos são ignorados, mas
  vale gravar alguns eventos reais (`/admin/events/{id}`) e conferir.

## Referências

- Zep: <https://help.getzep.com/concepts> · MCP de docs: `https://docs-mcp.getzep.com/mcp` (já em `.mcp.json`)
- Umbler Talk: <https://help.umbler.com/hc/pt-br/articles/14023563758861-Como-criar-Webhooks> · API: <https://app-utalk.umbler.com/api/docs/index.html>
- Base empírica: `../getzap-example/APRENDIZADOS-ZEP.md`
