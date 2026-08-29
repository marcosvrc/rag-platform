# rag-platform
Self-service RAG platform for document ingestion, hybrid retrieval, grounded generation, evaluation, governance, and observability.

## Status

Este repositório está sendo construído a partir de
`rag-platform-llm-implementation-plan.md`, seguindo o backlog de atividades
(`RAG-XXX`) descrito nesse documento. Cada atividade é entregue em uma
branch/PR separada.

Concluído até o momento:

- **RAG-001 — Inicializar o repositório** (estrutura de diretórios e
  configuração Python de base).
- **RAG-002 — Padronizar qualidade de código** (Ruff, Mypy, Pytest e
  cobertura).
- **RAG-003 — Criar Docker Compose local** (PostgreSQL/pgvector, Redis,
  MinIO e stack de observabilidade).
- **RAG-004 — Implementar configuração da aplicação** (Pydantic Settings).
- **RAG-006 — Configurar banco e migrations** (SQLAlchemy async + Alembic
  + extensão pgvector).
- **RAG-005 — Criar API base e health checks** (FastAPI, `/health/live`,
  `/health/ready`).
- **RAG-070 — Workflow inicial de pull request** (GitHub Actions: lint,
  typecheck, testes, migrations e validação do OpenAPI).
- **RAG-010 — Modelar entidades e estados** (entidades de domínio,
  máquina de estados de `Document`).
- **RAG-011 — Criar schema inicial** (tabelas, constraints, FKs e
  índices do modelo mínimo, migration 0002).
- **RAG-071 — Adicionar segurança ao CI** (secret scanning, SAST, SCA e
  lint de Dockerfile, com governança de exceções).
- **RAG-013 — Tratamento padronizado de erros** (Problem Details,
  RFC 7807, com `request_id` de correlação).
- **RAG-012 — Implementar CRUD de bases de conhecimento** (criar,
  listar, consultar, atualizar e excluir logicamente; paginação por
  cursor; isolamento por tenant).
- **RAG-020 — Implementar porta de object storage** (interface +
  adapter MinIO/S3 via `aioboto3`, sanitização de key, checksum).

## Desenvolvimento local

Pré-requisitos: Python 3.12.

```bash
make install    # cria .venv e instala dependências de desenvolvimento
make lint       # ruff check + ruff format --check
make typecheck  # mypy sobre apps/, packages/, adapters/ e tests/
make test       # pytest com relatório de cobertura (term-missing + XML)
make run-api    # sobe a API (uvicorn, com reload) em http://localhost:8000
make check      # lint + typecheck + test — o "pipeline" local completo
```

### Cobertura de testes

`make test` sempre gera relatório de cobertura (`--cov-report=term-missing`
e `coverage.xml`). O gate mínimo (`fail_under`, ver
`[tool.coverage.report]` em `pyproject.toml`) ficou em `0` enquanto só
havia módulos vazios (RAG-002) e foi elevado para **85%** em RAG-004,
quando o primeiro código de aplicação real
(`packages/config/settings.py`) passou a existir — hoje em 100% de
cobertura.

## Configuração da aplicação (RAG-004)

Toda variável de ambiente da aplicação é lida e validada por
`packages/config/settings.py`, via Pydantic Settings — nenhum outro
módulo deve chamar `os.environ` diretamente. Ponto de entrada:
`get_settings()` (cacheado por processo).

- Campos com default (hosts, portas, nomes de banco) refletem os valores
  do `docker-compose.yml` (RAG-003) quando a API/worker rodam no host.
- Campos sem default (`POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD`) são
  obrigatórios: se ausentes — no ambiente ou em um `.env` — a aplicação
  falha na inicialização com `ConfigurationError`, cuja mensagem cita
  apenas o nome da variável faltante, nunca um valor.
- Segredos usam `pydantic.SecretStr`: `repr(settings)`/`str(settings)`
  sempre mostram `**********`, então mesmo um log ou `print` acidental
  não expõe credenciais.

## Banco de dados e migrations (RAG-006)

Com o PostgreSQL do `docker compose` (RAG-003) no ar e um `.env` válido
(RAG-004):

```bash
.venv/bin/alembic upgrade head    # aplica todas as migrations pendentes
.venv/bin/alembic downgrade -1    # desfaz a última
.venv/bin/alembic current         # mostra a revisão atual
```

A primeira migration (`0001`) apenas habilita a extensão `vector`
(`CREATE EXTENSION IF NOT EXISTS vector`) — as tabelas do modelo mínimo
(seção 9 do plano) são criadas em RAG-011. A URL de conexão nunca é
hardcoded em `alembic.ini`: `migrations/env.py` a monta a partir de
`packages/config/settings.py` (RAG-004), usando o mesmo engine
assíncrono (`asyncpg`) que a aplicação. `adapters/postgres/engine.py`
expõe `get_engine()`/`get_session_factory()`/`get_session()`
(cacheados por processo), consumidos pela aplicação a partir de RAG-012.

Para gerar o SQL das migrations sem se conectar a um banco (útil para
revisão em PR): `.venv/bin/alembic upgrade head --sql`.

## API (RAG-005)

Com os serviços do `docker compose` (RAG-003) no ar e um `.env` válido
(RAG-004), suba a API com `make run-api` e teste:

```bash
curl http://localhost:8000/health/live
# {"status": "ok"}

curl -i http://localhost:8000/health/ready
# 200 se Postgres, Redis e MinIO estiverem alcançáveis; 503 caso contrário,
# com o detalhe de qual dependência falhou (nunca stack trace ou credencial):
# {"status": "error", "checks": {"postgres": "ok", "redis": "error", "minio": "ok"}}
```

`/health/live` nunca depende de Postgres/Redis/MinIO — verifica só que o
processo está no ar, para não ser derrubado por uma falha temporária de
uma dependência (isso é papel do `/health/ready`). Endpoints de negócio
(`/v1/...`) chegam a partir de RAG-012.

## Tratamento de erros (RAG-013)

Qualquer erro em um endpoint de negócio vira
[Problem Details](https://www.rfc-editor.org/rfc/rfc7807) (RFC 7807,
`application/problem+json`), nunca uma stack trace:

```bash
curl -i http://localhost:8000/qualquer-rota-que-nao-existe
# HTTP/1.1 404 Not Found
# content-type: application/problem+json
# x-request-id: 3fa2c1c0-...
#
# {"type": "about:blank", "title": "Not Found", "status": 404,
#  "instance": "/qualquer-rota-que-nao-existe", "request_id": "3fa2c1c0-..."}
```

- `packages/application/errors.py`: categorias de erro independentes de
  HTTP (`NotFoundError`, `ConflictError`, etc.) — usadas pelos casos de
  uso, sem nenhum acoplamento a FastAPI.
- `packages/contracts/problem_details.py`: o schema `ProblemDetail`
  (Pydantic) que toda resposta de erro segue.
- `apps/api/errors.py`: traduz cada erro de aplicação (e também
  `DomainError`/`InvalidStatusTransitionError` de RAG-010, sempre como
  409; `HTTPException`; erros de validação do Pydantic; e qualquer
  exceção não tratada, como 500) para Problem Details, e atribui um
  `request_id` de correlação a cada requisição (lido de `X-Request-ID`
  se o cliente enviar um, senão gerado) — presente tanto no corpo quanto
  no header `X-Request-ID` da resposta, em qualquer status.
- `/health/live` e `/health/ready` (RAG-005) mantêm o próprio formato
  (`{"status": ..., "checks": {...}}`) — são um contrato à parte, não
  endpoints de negócio.

## Bases de conhecimento (RAG-012)

Endpoints de `/v1/knowledge-bases` (seção 10.1 do plano):

| Método | Rota | O que faz |
| --- | --- | --- |
| `POST` | `/v1/knowledge-bases` | cria (201) |
| `GET` | `/v1/knowledge-bases` | lista, paginado por cursor (200) |
| `GET` | `/v1/knowledge-bases/{id}` | consulta (200) |
| `PATCH` | `/v1/knowledge-bases/{id}` | atualização parcial (200) |
| `DELETE` | `/v1/knowledge-bases/{id}` | exclusão lógica (204) |

Decisões e limitações:

- **Isolamento por tenant via JWT autenticado (RAG-050/RAG-051).**
  `tenant_id` é resolvido a partir de um `Authorization: Bearer <token>`
  verificado (`apps/api/dependencies.py::get_current_tenant_id` →
  `get_current_identity` → `TokenVerifierPort`, RAG-050); token ausente,
  malformado, inválido, ou sem a claim `tenant_id`, vira 401. O
  cabeçalho `X-Tenant-Id` provisório do RAG-012 não existe mais — ver
  "Autorização e contexto do tenant (RAG-051)" abaixo.
- **Toda consulta ao banco recebe `tenant_id` explicitamente**
  (`packages/application/ports/knowledge_base_repository.py`). Uma
  base de outro tenant é tratada exatamente como inexistente — 404,
  nunca 403 — para não vazar a existência de recursos alheios.
- **Arquitetura em portas e adaptadores**: `KnowledgeBaseRepositoryPort`
  (`packages/application/ports/`) tem dois adapters —
  `InMemoryKnowledgeBaseRepository` (usado nos testes) e
  `PostgresKnowledgeBaseRepository` (usado pela API) — mesmo padrão do
  object storage (RAG-020). Comandos (escrita) e consultas (leitura)
  ficam em `packages/application/commands|queries/knowledge_base.py` e
  traduzem falhas do repositório para os erros de aplicação de RAG-013
  (`NotFoundError`, `ConflictError`, `UnprocessableEntityError`).
- **Paginação por cursor** (seção 8 do plano): o cursor é um par opaco
  `(created_at, id)` codificado como string; `GET
  /v1/knowledge-bases?limit=N&cursor=...` devolve até `N` itens e um
  `next_cursor` (`null` na última página).
- **`PATCH` é parcial de verdade**: só os campos enviados no corpo são
  alterados (`exclude_unset`). `description` aceita `null` explícito
  (limpa o campo); `name`/`config` enviados como `null` viram 422 —
  o domínio não permite nome vazio nem config nula.
- **`PostgresKnowledgeBaseRepository` não tem teste de integração
  neste sandbox** (mesma limitação já documentada em
  `adapters/postgres/engine.py`, RAG-006, e em `test_schema.py`,
  RAG-011: nenhum teste de PR chama infraestrutura real). O contrato
  da porta — incluindo os critérios de aceite desta atividade
  (paginação, isolamento por tenant) — é validado via
  `InMemoryKnowledgeBaseRepository`, que segue exatamente as mesmas
  regras.

## CI (RAG-070)

Toda pull request contra `master` roda `.github/workflows/pull-request.yml`,
com quatro jobs independentes (rodam em paralelo):

| Job | Equivalente local | O que valida |
| --- | --- | --- |
| Lint (Ruff) | `make lint` | formatação e regras de lint |
| Typecheck (Mypy) | `make typecheck` | tipagem estática |
| Testes unitários e OpenAPI | `make test` + `app.openapi()` | testes, cobertura >= 85%, schema OpenAPI válido |
| Validar migrations | `alembic upgrade head --sql` | grafo de revisões do Alembic, sem precisar de um banco real |

Os artefatos de teste (`pytest-report.xml`, `coverage.xml`) são publicados
no job de testes mesmo quando ele falha, para facilitar o diagnóstico.

**Importante — configuração manual pendente:** o workflow por si só não
bloqueia merge; isso depende de uma *branch protection rule* no GitHub
(Settings -> Branches -> Add rule para `master` -> "Require status checks
to pass before merging", marcando os quatro jobs acima). Esse passo
precisa ser feito por quem tem permissão de administrar o repositório.

Escopo desta atividade (RAG-070): apenas o workflow de PR. Secret
scanning/SAST/SCA (RAG-071), build e publicação de imagens no GHCR
(RAG-072) e o quality gate de avaliação RAG (RAG-073) ficam para
atividades seguintes, como o backlog já prevê.

## Segurança no CI (RAG-071)

`.github/workflows/security.yml` roda em toda PR contra `master`, em
paralelo a `pull-request.yml`, com cinco jobs:

| Job | Ferramenta | O que valida | Bloqueia quando |
| --- | --- | --- | --- |
| Secret scanning | [gitleaks](https://github.com/gitleaks/gitleaks) | histórico completo do git em busca de segredos | qualquer segredo encontrado |
| SAST | [bandit](https://bandit.readthedocs.io/) (`make security`) | código próprio (`apps`, `packages`, `adapters`) | achado de severidade **HIGH** (LOW/MEDIUM ficam visíveis no log, sem bloquear) |
| SCA | [pip-audit](https://github.com/pypa/pip-audit) (`make security`) | dependências instaladas contra bases de advisories conhecidas | qualquer vulnerabilidade conhecida |
| Lint de Dockerfile | [hadolint](https://github.com/hadolint/hadolint) | todo `Dockerfile*` do repositório | regra de nível **error** (ainda não há Dockerfile — este job passa trivialmente até o RAG-072 introduzir um) |
| Exceções de segurança | `scripts/check_security_exceptions.py` (`make security`) | `security/exceptions.yml` | entrada sem justificativa/prazo, ou prazo vencido |

Decisões e limitações:

- **Exceções exigem prazo e justificativa.** Uma supressão pontual (um
  `# nosec` do bandit, uma entrada na allowlist do `.gitleaks.toml`, um
  `--ignore-vuln` do pip-audit, uma regra em `ignored:` no
  `.hadolint.yaml`) só é legítima se tiver uma entrada correspondente
  em `security/exceptions.yml` com `justification` e `expires`
  (`AAAA-MM-DD`). O job falha a PR se uma entrada estiver incompleta
  ou vencida — a exceção precisa ser renovada ou o achado corrigido.
- **pip-audit trata toda vulnerabilidade conhecida como bloqueante.**
  As bases de advisories do ecossistema Python nem sempre expõem um
  nível de severidade consistente; tratar qualquer achado como
  bloqueante é o padrão mais seguro. Foi assim que esta atividade
  encontrou e corrigiu o PYSEC-2026-1845 (pytest < 9.0.3):
  o teto de versão do `pytest` em `pyproject.toml` foi elevado de
  `<9.0` para `<10.0`.
- **gitleaks e hadolint são binários baixados no workflow** (não
  pacotes Python), por isso não entram em `make security` — rode-os
  manualmente se quiser reproduzir localmente (versões pinadas em
  `.github/workflows/security.yml`).
- **Lint de Dockerfile ainda não tem o que escanear.** Não existe
  `Dockerfile` neste repositório até o momento (chega no RAG-072); o
  job localiza `Dockerfile*` dinamicamente e passa sem erro quando não
  encontra nenhum, para já estar pronto quando o RAG-072 adicionar os
  arquivos.

## Domínio (RAG-010)

`packages/domain` contém as regras de negócio puras do produto (sem
dependência de frameworks de infraestrutura), conforme a seção 9 do plano:

- `entities/`: `Tenant`, `KnowledgeBase`, `Document`, `DocumentVersion`,
  `Chunk`, `IndexJob`, `QueryLog` (+ `TokenUsage`), `QueryEvidence`,
  `Feedback`, `EvaluationRun`. Todas são modelos Pydantic imutáveis
  (`frozen=True`, sem campos extras).
- `enums/`: os enums de status/tipo usados pelas entidades acima
  (`DocumentStatus`, `TenantStatus`, `KnowledgeBaseStatus`,
  `ProcessingStatus`, `IndexJobType`, `FeedbackRating`).
- `exceptions/`: `DomainError` (base) e `InvalidStatusTransitionError`,
  levantada quando uma transição de estado não é permitida.
- `services/`: reservado para regras que orquestram múltiplas entidades;
  ainda vazio — a única máquina de estados desta atividade
  (`Document.transition_to`) vive na própria entidade.

Convenções compartilhadas (seção 8 do plano), aplicadas via tipos
`Annotated` em `entities/base.py`:

- `EntityId`: `UUID` que precisa ser versão 4.
- `UtcDateTime`: `datetime` que precisa ser timezone-aware e estar em UTC.

### Máquina de estados de `Document`

As transições permitidas seguem exatamente o diagrama da seção 9.1 do
plano:

```
PENDING -> PROCESSING -> INDEXED
                      -> FAILED
                      -> QUARANTINED
INDEXED -> PROCESSING -> INDEXED
Qualquer estado (exceto DELETED) -> DELETED
```

`Document.transition_to(novo_status)` devolve uma nova instância (a
entidade é imutável) ou levanta `InvalidStatusTransitionError` para
qualquer transição fora dessa lista — incluindo, deliberadamente, um
caminho de retry a partir de `FAILED`/`QUARANTINED`, que o plano não
descreve.

Rodar só os testes de domínio:

```bash
.venv/bin/pytest tests/unit/test_document.py tests/unit/test_domain_entities.py -q
```

## Schema inicial (RAG-011)

A migration `0002_create_core_schema` cria as 10 tabelas do modelo
mínimo (uma por entidade de RAG-010), a partir dos modelos ORM em
`adapters/postgres/models/` — modelos de persistência, não as entidades
de domínio (que continuam sem depender de SQLAlemy/pgvector, seção 5.1
do plano).

```bash
.venv/bin/alembic upgrade head        # com o compose (RAG-003) no ar
.venv/bin/alembic upgrade head --sql  # gera o SQL sem se conectar a nada
```

Decisões e limites conhecidos desta atividade:

- **Isolamento por tenant:** `knowledge_bases`, `chunks` e `query_logs`
  carregam `tenant_id` diretamente (NOT NULL, FK, índice próprio — são
  as únicas entidades de RAG-010 com esse campo). As demais tabelas
  (`documents`, `document_versions`, `index_jobs`, `feedbacks`,
  `query_evidences`) chegam até um tenant por join através de suas FKs.
  `tests/unit/test_schema.py` verifica isso a nível de schema; um teste
  de isolamento fim a fim contra um Postgres real fica para
  `tests/integration/`.
- **`chunks.embedding` não tem dimensão fixa ainda**: o modelo/alias de
  embeddings só é escolhido em RAG-025, e um índice pgvector (ivfflat/
  hnsw) exige uma dimensão fixa para existir. Por isso RAG-011 não cria
  esse índice — isso é RAG-030 ("usa índice pgvector"). Pelo mesmo
  motivo, a busca lexical (RAG-031, "índice GIN utilizado") também não
  ganha aqui uma coluna `tsvector`/índice GIN.
- `documents.active_version_id` e `document_versions.document_id` se
  referenciam mutuamente; a FK circular usa `use_alter=True` (ver
  comentário em `adapters/postgres/models/document.py`).
- Enums do domínio (RAG-010) viram `VARCHAR + CHECK` no banco
  (`native_enum=False`), não um tipo `ENUM` nativo do Postgres — mais
  simples de alterar depois (adicionar um valor é só migrar o CHECK).

## Armazenamento de objetos (RAG-020)

`packages/application/ports/object_storage.py` define `ObjectStoragePort`
(upload/download/delete) — casos de uso futuros (RAG-021+) dependem só
dela, nunca de um SDK de storage concreto (seção 5.1 do plano). Dois
adapters implementam a mesma porta:

- `adapters/object_storage/s3_object_storage.py` (`S3ObjectStorage`):
  implementação real, via `aioboto3`, contra o MinIO do `docker compose`
  (RAG-003) — funciona igual contra um S3 de verdade, só o
  `endpoint_url` muda.
- `adapters/object_storage/in_memory.py` (`InMemoryObjectStorage`): fake
  em memória para testes/desenvolvimento local, sem precisar de MinIO no
  ar.

Decisões desta atividade:

- **Sanitização de key** (`sanitize_object_key`, no módulo da porta —
  decidir o que é uma key segura independe de qual adapter a implementa):
  normaliza unicode, remove segmentos de path traversal (`.`/`..`),
  troca caracteres fora de `[\w.-]` por `_` e rejeita (`InvalidObjectKeyError`)
  um nome que sanitize para vazio ou exceda 1024 bytes.
- **Checksum**: `upload()` sempre devolve o SHA-256 calculado sobre os
  bytes enviados (`StoredObject.checksum_sha256`) — quem chama compara
  com o checksum esperado (ex.: `Document.checksum`, RAG-010) para
  detectar corrupção; a porta em si não tem um checksum "esperado" para
  validar sozinha.
- **Exclusão idempotente**: `delete()` de uma key que não existe não é
  erro (contrato da porta, refletido nos dois adapters).

Validação: como não tenho um MinIO real acessível daqui, `S3ObjectStorage`
é testado com o cliente `aioboto3` mockado (`tests/unit/test_object_storage_s3.py`)
— prova que o adapter monta as chamadas certas e traduz `ClientError`
(`NoSuchKey`) para `ObjectNotFoundError`, não que o MinIO/S3 real
funciona. Confirme upload/download/delete de verdade com o compose no
ar (RAG-003).

## Serviços locais (RAG-003)

```bash
cp .env.example .env   # opcional: ajuste credenciais/portas
docker compose up -d
docker compose ps      # aguarde todos os serviços ficarem "healthy"
```

| Serviço | Imagem | Porta padrão | Credenciais (dev) |
| --- | --- | --- | --- |
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` | `5432` | `rag_platform` / `rag_platform_local_only` |
| Redis | `redis:7.2-alpine` | `6379` | — |
| MinIO (API / console) | `minio/minio` | `9000` / `9001` | `rag_platform` / `rag_platform_local_only` |
| OpenTelemetry Collector (gRPC / HTTP / métricas) | `otel/opentelemetry-collector-contrib` | `4317` / `4318` / `8889` | — |
| Prometheus | `prom/prometheus` | `9090` | — |
| Grafana | `grafana/grafana` | `3000` | `admin` / `rag_platform_local_only` |

Todas as portas e credenciais são configuráveis via `.env` (ver
`.env.example`) e nunca devem ser reaproveitadas fora do ambiente local.
Os dados de cada serviço são persistidos em volumes Docker nomeados
(`postgres_data`, `redis_data`, `minio_data`, `prometheus_data`,
`grafana_data`); `docker compose down -v` remove tudo, inclusive os dados.

O Grafana já vem com o Prometheus provisionado como datasource; dashboards
específicos da aplicação são adicionados em RAG-053. A extensão `vector`
do PostgreSQL e as migrations do schema são responsabilidade de RAG-006 —
neste momento a imagem apenas a disponibiliza, sem criá-la.

A validação de configuração completa da aplicação (Pydantic Settings) é
entregue em RAG-004; a API e os workers que efetivamente usam estes
serviços chegam a partir de RAG-005.

## Upload de documentos (RAG-021)

`POST /v1/knowledge-bases/{knowledge_base_id}/documents` (multipart,
campo `file`) implementa os passos 1-5 do fluxo de indexação (seção 11
do plano): valida extensão/MIME type/tamanho, calcula SHA-256, detecta
duplicidade, armazena o arquivo original (`ObjectStoragePort`, RAG-020)
e cria `Document` (`PENDING`) + `DocumentVersion` (v1) + `IndexJob`
(`INDEX`, `PENDING`) numa única transação — devolve `202 Accepted`. O
job criado é publicado na fila (passo 6, RAG-022 — ver seção abaixo)
logo em seguida, exceto numa repetição idempotente (nada novo foi
criado, o job original já está — ou já foi — na fila).

Formatos aceitos (fixos, seção 2 do plano — não configuráveis por
ambiente): PDF, Markdown, TXT e DOCX; a extensão do arquivo precisa
corresponder ao `Content-Type` declarado. `DOCUMENT_MAX_SIZE_BYTES`
(`.env`, padrão 50 MiB) limita o tamanho — ambos violam com `422`.

**Duplicidade**: um checksum já usado (não excluído) na mesma base
retorna `409`. **Idempotência** (seção 8: "endpoints de criação devem
aceitar Idempotency-Key"): o cabeçalho `Idempotency-Key` faz uma
repetição da mesma requisição (mesmo nome/tipo/conteúdo) devolver o
mesmo documento/versão/job já criados, sem duplicar nada; a mesma chave
reusada para uma requisição diferente retorna `409`. O mapeamento vive
em `document_idempotency_keys` (migration 0003) — não é uma entidade de
domínio, é infraestrutura da aplicação.

`packages/application/ports/document_repository.py` define
`DocumentRepositoryPort`; `adapters/document_repository/postgres.py`
documenta a limitação conhecida sob corrida genuína de
`Idempotency-Key` (o caminho comum — retry sequencial — funciona
corretamente; uma corrida verdadeiramente simultânea pode deixar um
documento órfão, embora a resposta HTTP nunca divirja entre as duas
requisições).

Testes: `tests/unit/test_document_repository_in_memory.py` (contrato
da porta), `tests/unit/test_document_upload_command.py` (validação,
duplicidade, idempotência) e `tests/unit/test_document_router.py`
(visão HTTP, isolamento por tenant).
## Fila e worker de indexação (RAG-022)

Implementa os passos 6-7 do fluxo de indexação (seção 11 do plano):
publicar o `IndexJob` criado pelo RAG-021 numa fila e o worker adquirir
um lock idempotente antes de processá-lo. A extração/normalização/
chunking/embeddings/persistência em si (passos 8-14) ainda não existem
— isso é RAG-023 a RAG-027.

`packages/application/ports/job_queue.py` define `JobQueuePort`
(`enqueue_index_job`) — só o `id` do job é publicado, nunca o payload;
`adapters/queue/celery_job_queue.py` (`CeleryJobQueue`) publica via
Celery/Redis (broker e result backend = `Settings.redis_url`).
`adapters/queue/celery_app.py` mantém uma única app Celery compartilhada
entre produtor (API) e consumidor (worker), sem broker configurado até
`configure_celery_app(settings)` ser chamada explicitamente por quem
tem um `Settings` válido — nunca no import do módulo, para que
`adapters.queue`/`apps.indexing_worker` continuem importáveis sem
nenhuma infraestrutura (RAG-001).

`apps/indexing_worker/tasks.py` (`process_index_job_task`) é a task
Celery: um adapter fino que só traduz a contagem de tentativas do
Celery (`self.request.retries`) para uma chamada a
`packages/application/commands/index_job.py::process_index_job_attempt`
— a lógica de negócio de verdade, testável sem Celery nenhum:

- Primeira tentativa: reivindica o job (`claim_index_job`, transição
  atômica `PENDING -> RUNNING`) — o lock idempotente do passo 7. Se já
  foi reivindicado por outro worker (ou não existe mais), não processa.
- Sucesso: `mark_index_job_succeeded`.
- Falha com tentativas restantes: registra a tentativa
  (`mark_index_job_failed`, `final=False`) e levanta
  `RetryableIndexJobError`, que a task Celery reagenda automaticamente
  (`autoretry_for`) com backoff exponencial (`retry_backoff=True`,
  jitter, teto de 10 min) — 5 tentativas no total, um ponto de partida
  razoável (o plano não especifica o número).
- Falha na última tentativa: registra como definitiva
  (`mark_index_job_failed`, `final=True`, `status=FAILED`) e não
  reagenda mais — o critério de aceite "falha definitiva é registrada".

O processamento em si (`DocumentProcessorPort.process`) ainda não tem
implementação real: `adapters/document_processor/not_implemented.py`
é o placeholder usado em produção até o RAG-023 existir — todo job
enfileirado hoje falha definitivamente de propósito, nunca "sucede"
silenciosamente sem processar nada.

`apps/indexing_worker/worker.py` é o ponto de entrada real do processo
(`celery -A apps.indexing_worker.worker worker`) — só ele lê `Settings`
de verdade; `apps/indexing_worker/tasks.py` e `adapters/queue/celery_app.py`
continuam importáveis sem nenhuma configuração.

Testes: `tests/unit/test_index_job_processing.py` (reivindicação,
sucesso, retry, falha definitiva — sem Celery), `tests/unit/test_celery_job_queue.py`
(adapter produtor) e `tests/unit/test_indexing_worker_task.py` (fiação
da task Celery: nome registrado, configuração de retry, repasse de
`self.request.retries`).
## Autenticação JWT (RAG-050)

`packages/application/ports/token_verifier.py` define `TokenVerifierPort`
(`verify(token) -> TokenClaims`) — casos de uso e a API dependem só dela,
nunca de PyJWT ou de um SDK de IdP concreto (seção 5.1 do plano).
`adapters/token_verifier/pyjwt_verifier.py` (`PyJWTTokenVerifier`) é a
implementação via PyJWT: valida assinatura, issuer, audience e
expiração (com tolerância de relógio configurável, `JWT_LEEWAY_SECONDS`)
e extrai `subject`/`tenant_id`/`issuer`/`expires_at`; qualquer falha
vira `AuthenticationError` (RAG-013) com um detalhe genérico — nunca diz
*por que* o token falhou, para não dar a um atacante um oráculo.

Configuração (`packages/config/settings.py`, `.env.example`):
`JWT_ALGORITHM` (padrão `HS256`), `JWT_SECRET` (obrigatório para
algoritmos `HS*`), `JWT_PUBLIC_KEY` (obrigatório para `RS*`/`ES*`/`PS*`),
`JWT_ISSUER` e `JWT_AUDIENCE` (obrigatórios, sem default — como as
senhas de RAG-004, forçam configuração explícita em vez de um valor
"que sempre funciona").

**Modo local simulado** (seção 13 do plano: "em modo local, provedor de
identidade simulado e explicitamente identificado como não produtivo"):
não há OIDC real, apenas um segredo compartilhado (`JWT_SECRET`,
HS256) configurado via `.env`. `scripts/mint_local_dev_token.py` gera
tokens válidos para testar a API localmente:

```bash
python scripts/mint_local_dev_token.py --subject dev-user \
    --tenant-id 11111111-1111-1111-1111-111111111111
```

Nunca reutilize `JWT_SECRET`/`JWT_ISSUER` de desenvolvimento em
development ou production — lá, use um algoritmo assimétrico
(`JWT_ALGORITHM=RS256` + `JWT_PUBLIC_KEY` da chave pública do IdP real)
com o segredo gerenciado por secret manager.

Esta atividade entrega a verificação do token (assinatura, issuer,
audience, expiração — critério de aceite desta atividade). A troca de
`get_current_tenant_id` para resolver o tenant a partir de um token
verificado (em vez do cabeçalho `X-Tenant-Id` provisório do RAG-012), e
a prova de ausência de vazamento entre tenants, são RAG-051 (próxima
seção).

Testes: `tests/unit/test_token_verifier.py` (assinatura errada, issuer/
audience errados, expiração, leeway, claims obrigatórias ausentes,
`tenant_id` malformado, confusão de algoritmo, erros de configuração) e
`tests/unit/test_mint_local_dev_token.py`.
## Autorização e contexto do tenant (RAG-051)

`apps/api/dependencies.py` agora resolve identidade e tenant sempre a
partir de um JWT autenticado, nunca de um cabeçalho não verificado:

- `get_current_identity` exige `Authorization: Bearer <token>` e
  delega a verificação a `TokenVerifierPort` (RAG-050) — cabeçalho
  ausente, esquema diferente de `Bearer`, ou token que `verify()`
  rejeite, viram 401.
- `get_current_tenant_id` depende de `get_current_identity` e exige que
  a claim `tenant_id` esteja presente — a porta permite `tenant_id:
  None` (nem todo token de acesso precisa identificar um tenant), mas
  todo endpoint de negócio desta API opera em nome de exatamente um
  tenant, então esta função torna a claim obrigatória. Um token válido
  sem `tenant_id` também vira 401 (não é uma questão de permissão — o
  token não carrega a informação mínima exigida).

A assinatura usada pelos routers (`Depends(get_current_tenant_id)`) não
mudou — só a implementação por trás dela, exatamente como planejado no
RAG-050. `packages/application/ports/knowledge_base_repository.py` e
`document_repository.py` já exigiam `tenant_id` explicitamente em todo
método desde o RAG-012/RAG-021; esta atividade não precisou alterá-los.

O cabeçalho `X-Tenant-Id` provisório do RAG-012 foi removido — chamadas
à API agora exigem um JWT válido (ver "Autenticação JWT (RAG-050)"
acima para como mintar um token local com `scripts/mint_local_dev_token.py`).

Testes: `tests/unit/test_dependencies.py` (unidade — cabeçalho ausente,
esquema inválido, token inválido, token sem `tenant_id`, resolução
correta da identidade/tenant) e as suítes de isolamento entre tenants
em `tests/unit/test_knowledge_base_router.py` e
`tests/unit/test_document_router.py`, migradas de `X-Tenant-Id` para
tokens JWT reais.
## Prompt de resposta (RAG-040)

`config/prompts/answer.v1.yaml` é o prompt de resposta fundamentada,
versionado por convenção (seção 8 do plano): uma versão publicada é
imutável, uma mudança de conteúdo sempre cria `answer.v2.yaml`, nunca
edita a existente. `packages/generation/prompts.py::load_prompt(id,
version)` carrega e valida esse YAML — nada aqui assume "a versão
atual" implicitamente, todo carregamento pede `id`/`version`
explícitos; `get_default_answer_prompt()` é o único lugar que decide
qual versão a aplicação usa hoje.

O prompt é estruturado em campos, não um texto livre único, para que os
requisitos de aceite sejam verificáveis independentemente:

- `system_template`: instrução geral (responder só com base no
  contexto fornecido).
- `untrusted_context_notice`: declara que o conteúdo recuperado é dado,
  não instrução — qualquer comando embutido nos documentos deve ser
  ignorado (requisito de segurança da seção 13: conteúdo recuperado
  nunca deve ser tratado como instrução).
- `citation_instruction`: exige citação do `chunk_id` para toda
  afirmação relevante.
- `no_evidence_response`: resposta fixa para quando não há evidência
  suficiente (seção 12.1) — nunca inventar uma resposta.

`PromptTemplate` (Pydantic, `frozen=True`) valida que nenhum desses
campos é vazio e que `id`/`version` internos do YAML batem com o nome
do arquivo. `render(context, question)` só concatena essas partes —
seleção/priorização de evidência por orçamento de tokens (RAG-041) e a
chamada ao modelo (RAG-042) são responsabilidade de atividades
seguintes.

Testes: `tests/unit/test_prompts.py` cobre carregamento do `answer.v1`
real, os três requisitos de aceite acima, erro (`PromptNotFoundError`)
para id/versão inexistente, erro de inconsistência id/versão-vs-nome-de-arquivo,
cache por `(id, version)` e imutabilidade.

## Extração de conteúdo (RAG-023)

Implementa o passo 8 do fluxo de indexação (seção 11 do plano):
extrair texto e metadados de um documento já em memória (baixado do
object storage por quem chama — esta atividade não decide isso,
fica para o pipeline orquestrador do RAG-024/RAG-027 usar). Normalização,
chunking, embeddings e persistência (passos 9-14) continuam sem
implementação — RAG-024 a RAG-027.

`packages/application/ports/document_parser.py` define
`DocumentParserPort` (`parse(filename, content, content_type) ->
ParsedDocument`) — o domínio e os casos de uso não importam Docling
diretamente, só esta porta. `ParsedDocument` carrega o texto extraído
já normalizado para Markdown, a contagem de páginas (`None` quando o
formato não pagina, como Markdown/texto puro/DOCX) e o mimetype
original. Erros são categorizados em duas classes, ambas com
`content_type`/`detail` seguros para o chamador expor:
`UnsupportedDocumentFormatError` (formato que este parser não processa
— nunca, ou ainda não) e `DocumentParsingError` (formato suportado, mas
o conteúdo em si não pôde ser extraído — corrompido, malformado etc.).

`adapters/docling/parser.py` (`DoclingDocumentParser`) é a única
implementação hoje, para os quatro tipos aceitos no upload (RAG-021):
Markdown e texto puro (Docling trata texto puro como markdown trivial)
e DOCX extraem com sucesso; **PDF levanta `UnsupportedDocumentFormatError`
por enquanto** — não é uma decisão definitiva de excluir PDF, é uma
limitação temporária explicada em detalhe na docstring do módulo:

- O pipeline padrão de PDF do Docling baixa pesos de modelo em runtime
  — um modelo de detecção de **layout** do Hugging Face Hub sempre, e
  um modelo de **OCR** (RapidOCR) do ModelScope quando `do_ocr=True`.
  Isso foi testado nesta atividade: desabilitar OCR evita só o segundo
  download, não o primeiro — o modelo de layout não é OCR, é a
  extração de estrutura em si (o plano, seção 4.2, só exclui "OCR
  avançado" do escopo do POC).
- Nem o ambiente de dev local nem o ambiente onde isso foi pesquisado
  têm egress liberado para `huggingface.co`/`modelscope.cn` hoje.
- Por isso a dependência instalada é só
  `docling-slim[convert-core,format-markdown,format-docx,format-pdf]`
  (~450MB) em vez do extra `standard`/`all` (~5,5GB, que traz
  `torch`/`transformers`/`docling-ibm-models` para os modelos de ML). O
  extra `format-pdf` é necessário mesmo sem processar PDF porque
  `docling.document_converter` importa o backend de PDF
  incondicionalmente no nível de módulo — mas o `DocumentConverter`
  deste adapter nunca registra `InputFormat.PDF`, então nenhum modelo é
  carregado nem baixado.
- **Caminho para habilitar PDF depois** (não é trabalho desta
  atividade): pré-baixar os pesos uma vez com egress liberado
  (`docling-tools models download`), apontar
  `PdfPipelineOptions(artifacts_path=..., do_ocr=False)` para esse
  diretório (elimina o download em runtime), adicionar o extra
  `models-local` à dependência e decidir onde os pesos pré-baixados
  vivem em cada ambiente — essa última parte é uma decisão de deploy
  (RAG-07x), não de código.

Testes: `tests/unit/test_docling_parser.py` cobre os quatro formatos
do RAG-021 (Markdown, texto puro e DOCX extraindo com sucesso; PDF
levantando `UnsupportedDocumentFormatError`, sem tentar decodificar o
conteúdo), um `content_type` desconhecido e um DOCX corrompido
(`DocumentParsingError`, não `UnsupportedDocumentFormatError` —
distinção que o critério de aceite "erro de parsing é categorizado"
exige).

## Normalização e chunking (RAG-024)

Implementa os passos 9-10 do fluxo de indexação (seção 11 do plano):
normalizar o texto extraído (RAG-023) sem perder estrutura semântica e
dividi-lo em chunks determinísticos, seguindo os defaults da seção
11.1 (tamanho 500 tokens, sobreposição 75, mínimo 50). Embeddings e
persistência (passos 11-14) continuam sem implementação — RAG-025 a
RAG-027.

`packages/ingestion/chunking.py::chunk_document(markdown, *, title,
origin, config=None)` é a função pura: recebe o Markdown de UM
documento e devolve uma lista de `ChunkDraft` (um chunk ainda sem
`id`/`tenant_id`/`knowledge_base_id`/`version_id`/`embedding` — esses
campos só existem quando RAG-026 persiste o `Chunk` de domínio a
partir de um `ChunkDraft`). A assinatura só aceita um documento por
chamada — "não mistura documentos" é garantido estruturalmente, não
por convenção.

- **Seções e parágrafos**: o texto é dividido em blocos por título
  Markdown e por parágrafo; blocos da mesma seção são empacotados
  gulosamente até `chunk_size` tokens. Um chunk nunca combina blocos de
  seções diferentes — cada chunk tem exatamente uma seção de origem
  (`ChunkDraft.section`), o que preserva "seção" sem ambiguidade. Um
  parágrafo isolado maior que `chunk_size` é dividido por tokens
  diretamente (o fallback do passo 10), com sobreposição entre os
  pedaços. Um chunk normal pode terminar um pouco acima de
  `chunk_size` para acomodar um parágrafo inteiro — só o fallback por
  tokens respeita o limite à risca, porque não há mais parágrafo para
  preservar nesse caso.
- **Mínimo**: se o último chunk de uma seção fica abaixo de
  `min_chunk_size`, é fundido no chunk anterior da mesma seção (nunca
  cruza seção, nunca descarta conteúdo). A fusão remove a sobreposição
  já duplicada no início do chunk fundido antes de concatenar — sem
  isso, o trecho de sobreposição apareceria duas vezes.
- **Página**: nenhum formato suportado hoje pelo RAG-023 (Markdown,
  texto puro, DOCX) tem noção de página no Docling — `ChunkDraft.page`
  é sempre `None` na prática atual. O campo existe e é propagado de
  ponta a ponta para quando a extração de PDF paginada existir.
- **Configurável por base**: `ChunkingConfig.from_knowledge_base_config(kb.config)`
  lê `chunk_size`/`chunk_overlap`/`min_chunk_size` de `KnowledgeBase.config`
  (RAG-010), com os defaults da seção 11.1 para o que faltar —
  `InvalidChunkingConfigError` rejeita combinações inconsistentes
  (`chunk_overlap >= chunk_size`, `min_chunk_size > chunk_size`, etc.).
- **Contagem de tokens**: não usamos um tokenizer real (`tiktoken`) —
  ele baixa o vocabulário BPE em runtime na primeira chamada
  (`openaipublic.blob.core.windows.net`), o mesmo problema de egress já
  documentado para o modelo de layout do Docling (RAG-023).
  `_count_tokens` aproxima com uma contagem de palavras/pontuação via
  regex — determinística, sem download. É uma aproximação aceitável
  para chunking (o que importa é um tamanho consistente), mas vale
  reavaliar antes de reusar esse número para orçamento de contexto de
  geração (RAG-041) ou limites de lote de embeddings (RAG-025).

Testes: `tests/unit/test_chunking.py` cobre os critérios de aceite —
documento vazio, seções nunca misturadas, página sempre `None` hoje,
determinismo, validação de `ChunkingConfig`, `from_knowledge_base_config`,
o fallback por tokens (com verificação de sobreposição exata entre
pedaços consecutivos) e a fusão por mínimo sem duplicar a sobreposição
(inclusive com `chunk_overlap=0`).

## Embeddings via LiteLLM (RAG-025)

Implementa o passo 11 do fluxo de indexação (seção 11 do plano):
"gerar embeddings em lotes". Persistência e ativação de versão (passos
12-14) continuam sem implementação — RAG-026/027.

`packages/application/ports/embedding_provider.py` define
`EmbeddingProviderPort` (`embed(texts) -> list[list[float]]`, uma
embedding por texto, preservando a ordem) — domínio e casos de uso não
importam LiteLLM nem `httpx` diretamente. Erros são categorizados:
`EmbeddingTimeoutError` (timeout em todas as tentativas) e
`EmbeddingProviderUnavailableError` (qualquer outro erro do gateway —
HTTP >= 500, erro de conexão, corpo malformado, contagem de embeddings
divergente da de textos enviados — depois de esgotar as tentativas).

`adapters/litellm/embedding_provider.py` (`LiteLLMEmbeddingProvider`)
fala com o gateway LiteLLM (seção 5 do plano: "AI Gateway: LiteLLM")
por HTTP simples (`POST {base_url}/embeddings`, API compatível com
OpenAI que o LiteLLM expõe em modo proxy) usando `httpx` — não a SDK
Python `litellm`, que rotearia para provedores diretamente e
duplicaria o papel do gateway, reintroduzindo o tipo de dependência
pesada já evitada em RAG-023/024. Timeout por tentativa e retry com
backoff exponencial são configuráveis (`Settings.litellm_timeout_seconds`,
`litellm_max_retries`); um HTTP 4xx nunca é retentado (não é
transitório). Lotes de até `litellm_embedding_batch_size` textos por
requisição (default 100); a resposta é reordenada pelo campo `index`
de cada item, nunca assumindo que o gateway devolve na ordem enviada.

**O que fica de fora desta atividade, propositalmente**: nenhum proxy
LiteLLM real é provisionado (`docker-compose.yml` não ganhou um serviço
novo) — isso exigiria escolher e configurar credenciais de um provedor
de embeddings de verdade, o que não existia em lugar nenhum do
`.env.example` até agora. É uma decisão de produto (qual provedor, que
chave usar) que ficou para o usuário decidir explicitamente, não algo
para assumir sozinho. `Settings.litellm_base_url` já aponta para onde
o gateway deveria estar (`http://localhost:4000`, a porta padrão do
proxy LiteLLM) para quando isso for provisionado.

**Alias versionado**: `config/models/embedding.v1.yaml` (carregado por
`packages/config/models.py::get_default_embedding_model()`, mesma
convenção de `packages/generation/prompts.py`/RAG-040) declara o alias
que a aplicação usa — trocar o modelo por trás do alias é configuração
do gateway LiteLLM, não deste repositório; uma mudança que precise de
um alias novo cria `embedding.v2.yaml`, nunca edita o existente.

Testes: `tests/unit/test_litellm_embedding_provider.py` cobre os
critérios de aceite — lista vazia não chama o gateway, alias e textos
corretos são enviados, resposta fora de ordem é corrigida, batching
com preservação de ordem, retry com sucesso na tentativa seguinte,
esgotamento de retries por timeout/erro de servidor/erro de conexão,
erro de cliente (4xx) sem retry, corpo malformado, contagem
divergente, e header de autorização quando `LITELLM_API_KEY` está
configurado — tudo via `httpx.MockTransport`, sem chamar um serviço
real. `tests/unit/test_model_config.py` cobre o carregador de alias
(mesmos critérios de `test_prompts.py`).

## Persistência de chunks e ativação de versão (RAG-026)

Implementa os passos 8-14 do fluxo de indexação (seção 11 do plano):
extração, chunking, embeddings, persistência e ativação de versão,
tudo orquestrado por um único adapter real de `DocumentProcessorPort`
(RAG-022) — antes disso, `NotImplementedDocumentProcessor` fazia todo
`IndexJob` reivindicado falhar definitivamente de propósito.

`adapters/document_processor/pipeline.py` (`PipelineDocumentProcessor`)
não faz nenhuma extração/chunking/embeddings por conta própria: só
resolve `IndexJob` -> `Document` -> `DocumentVersion` mais recente ->
`KnowledgeBase`, chama o `DocumentParserPort` (RAG-023), a função pura
`chunk_document` (RAG-024, não é um port), o `EmbeddingProviderPort`
(RAG-025) e, por fim, persiste tudo via os métodos novos de
`DocumentRepositoryPort` desta atividade.

**Métodos novos em `DocumentRepositoryPort`** (todos sem filtro de
tenant — o worker resolve um `Document`/`KnowledgeBase` a partir de um
`document_id`/`index_job_id` isolado, antes de ter qualquer tenant
autenticado em mãos; mesma justificativa já usada por
`claim_index_job` no RAG-022):
- `get_index_job`, `get_document`, `get_latest_version`: leituras
  simples que o pipeline precisa para montar seu contexto.
- `mark_document_processing`: transiciona `Document.status` para
  `PROCESSING`, idempotente via guarda a nível de SQL
  (`WHERE status != PROCESSING`) — `Document.transition_to` rejeitaria
  `PROCESSING -> PROCESSING` como um self-loop não listado na máquina
  de estados (`packages/domain/entities/document.py`), e um
  reprocessamento (retry de job, ou reindexação futura do RAG-027)
  não pode falhar só por já estar em `PROCESSING`.
- `persist_chunks_and_activate_version`: substitui todos os chunks de
  uma `version_id` pelos novos, grava `extracted_object_key` e ativa a
  versão (`Document.active_version_id`, `status=INDEXED`) — tudo numa
  única transação/commit. Índice parcial nunca fica ativo (se qualquer
  passo do pipeline falhar antes deste método, nada muda no banco, e a
  versão ativa anterior — se houver — continua sendo a consultável).
  A estratégia é DELETE + INSERT (nunca diff): reprocessar a mesma
  `version_id` nunca duplica chunks, só substitui o conjunto inteiro.

Em `KnowledgeBaseRepositoryPort`, `get_by_id_unscoped` é a mesma
exceção deliberada, aplicada à base de conhecimento: o worker só
conhece `Document.knowledge_base_id`, nunca um tenant autenticado, e
precisa do `tenant_id`/`config` da base para montar os chunks e a
config de chunking. Nunca é exposto a um tenant diretamente — todo
caminho autenticado continua usando `get_by_id`.

**O que fica de fora desta atividade, deliberadamente**: se todas as
tentativas de um `IndexJob` se esgotarem (falha definitiva,
`packages/application/commands/index_job.py::process_index_job_attempt`,
já implementado e mesclado no RAG-022), o `Document` correspondente NÃO
é transicionado para `FAILED` — ele fica preso em `PROCESSING` (ou
`PENDING`, se a falha ocorrer antes do passo 2 do pipeline). Fechar essa
lacuna exigiria alterar `process_index_job_attempt` para propagar
`document_id` até o ponto onde a falha definitiva é decidida (hoje só
`index_job_id` está em escopo ali) — uma mudança em código já revisado
e mesclado antes desta atividade, feita sem o autor disponível para
revisar. Ficou registrado aqui como um follow-up conhecido em vez de
mudado silenciosamente; `IndexJob.status=FAILED` já é visível o
suficiente para um operador humano perceber e investigar o documento
travado enquanto isso não é resolvido.

Testes: `tests/unit/test_document_repository_in_memory.py` (classe
`TestRag026PersistChunksAndActivateVersion`) cobre os 5 métodos novos
de `DocumentRepositoryPort` contra o fake em memória — idempotência de
`mark_document_processing`, ativação atômica de versão, e
reprocessamento sem duplicar chunks.
`tests/unit/test_knowledge_base_in_memory_repository.py` cobre
`get_by_id_unscoped` (incluindo bases já excluídas — o método nunca
filtra por status). `tests/unit/test_pipeline_document_processor.py`
cobre a orquestração completa do `PipelineDocumentProcessor` com fakes
para as 5 portas: pipeline de ponta a ponta com sucesso, reprocessamento
idempotente, e os três casos defensivos (job sumido, documento sem
versão, base de conhecimento ausente).

## Status de indexação e reindexação (RAG-027)

Implementa o objetivo do épico E2 restante: expor o status de um job
de indexação ao cliente e permitir disparar uma nova indexação sem
subir um arquivo de novo.

`GET /v1/jobs/{index_job_id}` (prometido desde o RAG-021, ver o
docstring de `DocumentUploadResponse`) devolve estado e erros de um
`IndexJob`: `status`, `attempts`, `error_code`, `error_message`. Como
`IndexJob` não carrega `tenant_id` (só `document_id`), o isolamento é
transitivo — `packages/application/queries/document.py::
get_index_job_status` resolve `IndexJob` -> `Document` ->
`KnowledgeBase` (via `get_by_id_unscoped`, RAG-026) e só então compara
`KnowledgeBase.tenant_id` contra o tenant autenticado; um job de outro
tenant (ou inexistente) é sempre 404, nunca 403 (mesmo padrão do resto
da API).

`POST /v1/knowledge-bases/{id}/documents/{document_id}/reindex`
(`packages/application/commands/document.py::reindex_document`) dispara
uma reindexação: cria uma nova `DocumentVersion` (mesma `object_key` da
versão anterior — o conteúdo original nunca muda, só o processamento;
número de versão incrementado) + um novo `IndexJob` (tipo `REINDEX`,
já existente desde o RAG-010) e publica na fila, reaproveitando o novo
método `DocumentRepositoryPort.create_reindex_job` (RAG-027). Só é
permitida quando o documento está `INDEXED` — caso contrário, 409
(`Documento precisa estar indexado para poder ser reindexado`).

**"Consultas continuam disponíveis" (critério de aceite)**: disparar
uma reindexação não toca no `Document.status` nem em `active_version_id`
— eles só mudam quando o worker de fato processa o novo job e chama
`persist_chunks_and_activate_version` (RAG-026). Entre o disparo e essa
ativação, o cliente continua recebendo a versão ativa anterior em
qualquer consulta (retrieval, RAG-030+) — nunca um estado parcial da
reindexação em andamento.

Testes: `tests/unit/test_document_reindex_command.py` cobre os
critérios de aceite do comando (nova versão, reindexação só permitida
em `INDEXED`, documento/base de outro tenant nunca distinguível de
inexistente, e o mapeamento de `DocumentVersionConflictError` — corrida
rara entre duas reindexações do mesmo documento — para 409).
`tests/unit/test_document_status_query.py` cobre a consulta de status
e seu isolamento por tenant. `tests/unit/test_jobs_router.py` e as
novas funções em `tests/unit/test_document_router.py` cobrem a visão
HTTP dos dois endpoints (200/202/401/404/409).

## Busca lexical (RAG-031)

Implementa a recuperação de chunks por PostgreSQL Full Text Search —
o objetivo do épico E3 que não depende de nenhuma decisão de produto
ainda pendente (ao contrário do RAG-030, busca vetorial: continua
bloqueada porque o modelo/alias real de embeddings, e portanto sua
dimensão, ainda não foi escolhido por trás do gateway LiteLLM — decisão
deliberadamente adiada no RAG-025 — e um índice ANN pgvector exige
dimensão fixa. RAG-031 não tem essa dependência, por isso foi
implementada primeiro).

Migration 0004 adiciona `chunks.content_tsv`, uma coluna GERADA pelo
Postgres (`GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED`,
suportado desde o PostgreSQL 12) com um índice GIN — a aplicação nunca
escreve nela, o Postgres recalcula sozinho sempre que `content` muda.
Configuração `simple` (não `portuguese` nem outro idioma específico):
o conteúdo de um chunk pode estar em qualquer idioma que o tenant
carregar, e a plataforma não pergunta o idioma em nenhum lugar do
fluxo de upload — `simple` é um denominador comum seguro, sem
stemming específico de idioma; trocar para uma configuração dedicada
por base de conhecimento é uma decisão futura, não deste RAG.

`packages/application/ports/lexical_search.py` define `LexicalSearchPort`
(`search(tenant_id, knowledge_base_id, query, limit) -> list[ScoredChunk]`)
e `adapters/lexical_search/postgres.py` (`PostgresLexicalSearch`) a
implementa via `ts_rank` + o operador `@@` contra `content_tsv`,
compilado pelo SQLAlchemy para exatamente a forma que o planner do
Postgres casa com o índice GIN. Três critérios de aceite, todos no
próprio `WHERE`/`ORDER BY` da consulta, nunca em Python depois:
- **Filtros antes do ranking**: `chunks.tenant_id`/`chunks.knowledge_base_id`
  (já denormalizados na tabela, RAG-011) entram no `WHERE`.
- **Só a versão ativa**: um `JOIN` com `documents` em
  `documents.active_version_id == chunks.version_id` exclui chunks de
  qualquer versão superada por uma reindexação (RAG-026/027) — mesmo
  que ainda existam fisicamente na tabela (`persist_chunks_and_
  activate_version` não apaga chunks de versões antigas ao ativar uma
  nova, um efeito colateral de armazenamento conhecido e aceito, não
  uma falha de isolamento).
- **Ranking determinístico**: `ORDER BY ts_rank(...) DESC, chunks.id ASC`
  — o `id` como desempate estável, já que `ts_rank` sozinho não separa
  scores empatados.

`adapters/lexical_search/in_memory.py` (`InMemoryLexicalSearch`) é um
fake para testar só o contrato da porta (filtros de tenant/base,
ordenação determinística, `limit`) sem Postgres — não modela versões
nem "versão ativa" (`Chunk` não carrega `document_id`), e usa uma
contagem de termos simples em vez de `ts_rank`; quem monta um cenário
de teste com ele só deve indexar chunks que já representem uma versão
ativa, mesmo cuidado que qualquer teste com `InMemoryDocumentRepository`.

Testes: `tests/unit/test_lexical_search_in_memory.py` cobre o contrato
da porta (filtro por tenant, por base, ranking por número de termos,
desempate determinístico, `limit`, query sem termos relevantes).
`tests/unit/test_schema.py::test_chunks_content_tsv_has_a_gin_index`
verifica a coluna gerada e o índice GIN a partir de `Base.metadata`
(mesma abordagem sem banco real do RAG-011) — "índice GIN utilizado"
de fato (via `EXPLAIN` contra um Postgres real) fica para
`tests/integration/`, quando essa suíte existir, mesma limitação já
documentada para os demais adapters Postgres deste projeto.


## Auditoria de ações administrativas (RAG-054)

Registra um evento de auditoria (ator, tenant, ação, tipo/id de
recurso, timestamp) para cada ação administrativa que já existe hoje
na API: criar/atualizar/excluir base de conhecimento (RAG-012) e
enviar/reindexar documento (RAG-021/RAG-027). Escopo desta atividade:
"registrar", não "consultar" — um endpoint de leitura do trilho de
auditoria (para um painel administrativo, por exemplo) fica para uma
atividade futura, então `packages/application/ports/audit_log.py`
(`AuditLogPort`) só tem `record`.

Append-only por design, não por convenção: nem a porta nem nenhum
adapter (`adapters/audit_log/`) declara um método de atualização ou
remoção. `migrations/versions/0005_create_audit_events.py` cria
`audit_events` — `resource_id` não é uma foreign key (é polimórfico:
aponta para `knowledge_bases.id` OU `documents.id`, dependendo de
`resource_type`, e uma FK exigiria uma única tabela de destino);
`tenant_id` é FK normal para `tenants.id`, com o mesmo índice que
todas as outras tabelas multi-tenant deste schema (RAG-011). Não é
uma entidade de domínio (mesmo precedente de
`document_idempotency_keys`, RAG-021): infraestrutura de aplicação,
então só existe em `adapters/postgres/models/audit_event.py`, nunca
em `packages/domain/entities`.

Uma falha ao registrar um evento (ex.: banco indisponível) nunca deve
derrubar a ação administrativa que já teve sucesso — isso trocaria
uma falha de observabilidade por uma indisponibilidade real da API.
`record_audit_event_safely` (mesmo módulo da porta) é o que os
routers chamam em vez de `audit_log.record(...)` direto: registra a
falha via `logging` (nunca engole em silêncio) e sempre retorna
normalmente. `actor` vem de `TokenClaims.subject` (RAG-050/RAG-051,
`Depends(get_current_identity)`, já injetado nos endpoints
existentes ao lado de `Depends(get_current_tenant_id)` — mesma
identidade, sem verificar o token duas vezes).

`adapters/audit_log/in_memory.py` (`InMemoryAuditLog`) expõe
`events: list[AuditEvent]` — não faz parte da porta, é só para os
testes inspecionarem o que foi registrado (mesmo padrão de
`InMemoryLexicalSearch.index_chunk`, RAG-031).

Testes: `tests/unit/test_audit_log.py` cobre o contrato da porta e
`record_audit_event_safely` (inclusive que uma falha de gravação
nunca propaga); `tests/unit/test_schema.py` cobre o formato da tabela
(colunas obrigatórias, `resource_id` sem FK); e cada router de teste
(`test_knowledge_base_router.py`, `test_document_router.py`) tem uma
classe `TestAuditLog` provando que a ação HTTP correspondente
registrou exatamente um evento com o ator/tenant/ação/recurso
esperados.
