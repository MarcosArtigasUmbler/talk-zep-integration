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
| Atendente responsável | **`graph.add_fact_triple`** `ATENDIDO_POR` | Dado que já chega estruturado não passa pelo LLM: elimina erros de extração. |
| Setor, canal, tags, chat aberto/fechado | Store (MongoDB) + metadata do usuário e das mensagens | Estado operacional e volátil, não memória. O `/briefing` devolve o estado atual; a metadata das mensagens é filtrável na busca do grafo. |
| Conhecimento da Umbler | Grafo avulso `ZEP_ORG_GRAPH_ID` | Legível pelo agente de qualquer contato. **Nunca dado de cliente.** |

**Por que tags não viram nós.** São muitas, mudam o tempo todo e não são
determinísticas. No grafo virariam dezenas de fatos "tem a tag X" defasados,
competindo com fatos úteis na busca. Ficam na metadata do usuário no Zep e no
Store, sempre com o estado atual. Se uma tag específica se provar importante
como memória, dá para adicioná-la como fato depois.

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
| `NewChat` | Cria a thread; fato `ATENDIDO_POR` se já há atendente. |
| `MemberTransfer` | Fato `ATENDIDO_POR` com o novo atendente. |
| `ChatSectorChanged`, `ChatClosed` | Atualizam o estado do chat no Store (setor, aberto/fechado). |
| `ChatTagChanged` | Atualiza tags na metadata do usuário no Zep e no Store. |
| `ChatPrivateStatusChanged` | Só atualiza o estado do chat. |

Independentemente do tipo, todo evento sincroniza a ficha do contato (só se
mudou), o estado do chat e o atendente responsável (só se mudou). Conversas
internas entre membros são ignoradas; grupos ficam fora por padrão
(`INGEST_GROUP_CHATS`).

### Contrato do webhook e a fila

O Talk exige **2xx em menos de 5 segundos**, reenvia até 2 vezes com o mesmo
`EventId` (header `x-attempt`) e pausa o webhook após 100 falhas no dia. Por
isso o handler HTTP só grava o evento no Store e enfileira; os workers falam
com o Zep depois, com retentativa exponencial (`MAX_ATTEMPTS`). Evento
reentregue é reconhecido pelo `EventId` e ignorado. Na subida, eventos que
ficaram pendentes voltam para a fila.

### Persistência

O Store guarda o que o Zep não guarda por nós: diário de eventos
(idempotência e fila durável), mensagens e fatos já enviados, ficha e tags do
contato, chat → thread com setor e atendente atuais, e nomes de atendentes.

É **MongoDB em todos os ambientes**, sem alternativa local: `MONGODB_URI`
(com credenciais embutidas, ou `MONGODB_USERNAME`/`MONGODB_PASSWORD` à parte)
e `MONGODB_DB` (padrão `talk_zep`). Coleções: `events`, `messages`, `facts`,
`contacts`, `chats`, `members`, com o identificador natural em `_id`. A
interface está em `app/pipeline/store/base.py`.

Os testes usam a mesma instância, num banco descartável `talk_zep_test_<hex>`
criado no início da sessão e apagado no fim. Para apontá-los a outra
instância, defina `MONGODB_TEST_URI`.

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
Instruções customizadas só existem nos planos Flex Plus e Enterprise; em
outros planos o passo 3 avisa e o restante do setup segue válido. Depois suba
a API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Documentação interativa em `/docs`. Rode os testes com `pytest -q`: não usam o
Zep real (há um fake em `tests/conftest.py`), mas precisam do MongoDB do
`.env`, onde criam e apagam um banco descartável.

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

Definida em `app/zep/ontology.py`. O tipo embutido `User` é o contato. A
ontologia descreve o **domínio de venda**, não a fonte: e-mail e Pipedrive
entrarão depois usando os mesmos tipos.

| Entidades (9) | Arestas (9) |
|---|---|
| `Vendedor` | `ATENDIDO_POR` User → Vendedor |
| `EmpresaCliente` | `TRABALHA_EM` User → EmpresaCliente |
| `ProdutoUmbler` | `USA` User/Empresa → Produto/Plano (já é cliente) |
| `Plano` | `AVALIA` User/Empresa → Produto/Plano (ainda não comprou) |
| `Negociacao` | `TEM_NEGOCIACAO` User/Empresa → Negociacao |
| `Necessidade` | `TEM_NECESSIDADE` User → Necessidade |
| `Objecao` | `LEVANTOU_OBJECAO` User → Objecao |
| `Concorrente` | `COMPARA_COM` User/Negociacao → Concorrente |
| `Compromisso` | `COMBINOU` User/Vendedor → Compromisso |

O limite de tipos é por plano e vale separadamente para entidades e arestas:
Free 5, Flex 10, Flex Plus 20. Esta ontologia foi dimensionada para o
**Flex**, com 1 + 1 de folga. `ATENDIDO_POR` é escrita por nós via
`fact_triple`; `TEM_NEGOCIACAO` está reservada para deals do Pipedrive; as
demais são dicas para o extrator. `GET /ontology` mostra o drift entre código
e Zep; a API loga um aviso na subida.

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
  pipeline/store/       Store: base.py (interface) e mongo.py (MongoDB, todos os ambientes)
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
tests/                  Zep falso em conftest.py; MongoDB real num banco descartável
knowledge/              arquivos de conhecimento da Umbler
```

## Implantação

Container Docker com **um único processo** e MongoDB como Store:

- Rode `uvicorn` sem `--workers` (ou com `--workers 1`). A fila é em memória
  e é reidratada do Mongo na subida; dois processos processariam o mesmo
  evento duas vezes.
- Uma réplica só, por enquanto. O diário no Mongo já é compartilhável; para
  várias réplicas falta apenas trocar a fila em memória por uma reivindicação
  atômica no Mongo (`findOneAndUpdate` de `pending` para `processing`).
- `MONGODB_URI` é obrigatória: sem ela a aplicação não sobe.

## Limitações conhecidas

- **Latência do Zep.** Extração assíncrona: fatos e contexto levam minutos.
  Nunca espere a ingestão dentro do webhook.
- **Troca de atendente.** Enviamos o fato novo com `valid_at`; a invalidação
  do anterior fica a cargo do Zep (contradição). O Store guarda o último
  estado por chat, então reenvios não duplicam.
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
