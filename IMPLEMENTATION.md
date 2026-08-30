# rag-platform — notas de implementação

Este arquivo detalha, atividade por atividade (`RAG-XXX`, conforme o
backlog de `rag-platform-llm-implementation-plan.md`), as decisões de
design, limitações conhecidas e cobertura de testes de cada
funcionalidade já implementada. A visão geral do projeto — o que é,
tecnologias, arquitetura e estrutura de diretórios — está no
[README.md](README.md); este arquivo existe para não sobrecarregar o
README com o histórico de decisões de cada atividade, que só interessa
a quem vai alterar ou revisar o código dessa área específica.

Cada seção abaixo corresponde a uma branch/PR já mesclada (ou, quando
indicado, a uma PR ainda aberta) — a mesma unidade de entrega usada em
todo o projeto.

## Progresso do backlog

34 das 48 atividades do plano estão mescladas em `master`. As 14
atividades restantes:

- **E4 — Geração fundamentada**: RAG-042 (geração via LiteLLM),
  RAG-043 (validação de groundedness/citações), RAG-044 (endpoint
  `query`), RAG-045 (feedback).
- **E6 — Avaliação RAG**: RAG-061 (avaliação de retrieval), RAG-062
  (avaliação de geração), RAG-063 (baseline da POC).
- **E7 — GitHub Actions e entrega**: RAG-073 (quality gate de
  avaliação RAG), RAG-074, RAG-075.
- **E8 — Finalização**: RAG-080, RAG-081, RAG-082, RAG-083.

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
| Lint de Dockerfile | [hadolint](https://github.com/hadolint/hadolint) | todo `Dockerfile*` do repositório (`Dockerfile.api`/`Dockerfile.worker`, RAG-072) | regra de nível **error** |
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
- **Lint de Dockerfile escaneia `Dockerfile.api`/`Dockerfile.worker`**
  (RAG-072). O job continua localizando `Dockerfile*` dinamicamente em
  vez de nomear os arquivos — passa sem erro se um dia nenhum existir,
  sem exigir mudança no workflow quando um novo Dockerfile aparecer.

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

**O que ficou de fora desta atividade, propositalmente**: nenhum proxy
LiteLLM real foi provisionado nesta atividade (`docker-compose.yml` não
ganhou um serviço novo) — isso exigiria escolher e configurar
credenciais de um provedor de embeddings de verdade, o que não existia
em lugar nenhum do `.env.example` até então. Era uma decisão de produto
(qual provedor, que modelo) que ficou para o usuário decidir
explicitamente, não algo para assumir sozinho. `Settings.litellm_base_url`
já apontava para onde o gateway deveria estar (`http://localhost:4000`,
a porta padrão do proxy LiteLLM) para quando isso fosse provisionado —
o que aconteceu no RAG-030 (ver seção abaixo), quando essa decisão foi
tomada.

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
o objetivo do épico E3 que não dependia de nenhuma decisão de produto
pendente (ao contrário do RAG-030, busca vetorial: na época, continuava
bloqueada porque o modelo/alias real de embeddings, e portanto sua
dimensão, ainda não tinha sido escolhido por trás do gateway LiteLLM —
decisão deliberadamente adiada no RAG-025 — e um índice ANN pgvector
exige dimensão fixa. RAG-031 não tinha essa dependência, por isso foi
implementada primeiro; RAG-030 resolveu essa decisão depois — ver seção
abaixo).

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
## Tracing distribuído (RAG-052)


Instrumenta a API e o worker de indexação com OpenTelemetry, cobrindo
o fluxo upload -> indexação de ponta a ponta (a API publica o
`IndexJob` no Celery, o worker consome) e, automaticamente, qualquer
fluxo futuro (ex.: `/v1/query`, RAG-044): a instrumentação é aplicada
uma vez, no nível da app FastAPI e da app Celery compartilhada, nunca
por rota ou por task — nada precisa mudar quando novos endpoints ou
tasks forem adicionados.

`packages/observability/tracing.py` concentra toda a configuração.
Decisão deliberada: ela lê variáveis `OTEL_*` diretamente
(`os.getenv`), não via `packages.config.settings.Settings` — a
primeira, porque `configure_tracing()` roda dentro de
`create_app()` (`apps/api/main.py`), e esse módulo nunca chama
`get_settings()` (vários testes importam a app sem nenhuma variável
de ambiente de negócio configurada, incluindo um teste que existe
justamente para provar que a liveness independe de configuração); a
segunda, porque `OTEL_EXPORTER_OTLP_*` já seguem a especificação
padrão do OpenTelemetry, e o próprio `OTLPSpanExporter()` já as lê
sozinho — reimplementar isso via `Settings` só duplicaria lógica do
SDK. `OTEL_TRACES_ENABLED` (não é uma variável do OpenTelemetry) é o
único interruptor nosso: default `false` — sem ele, a instrumentação
continua ativa, mas contra o tracer no-op padrão do OpenTelemetry
(nenhum `TracerProvider` real): zero overhead, zero thread em
background, zero chamada de rede, o que é o que mantém os testes
unitários passando sem nenhum Collector real no ar (seção 1 do
plano). `docker-compose.yml`/`.env.example` já ligam essa variável
para desenvolvimento local — como o `run-api`/`run-worker` do
Makefile rodam no host (RAG-003), essas variáveis só chegam ao
processo se estiverem no ambiente real do shell, não só em `.env`
(Pydantic Settings lê `.env` diretamente; um `os.getenv` puro não);
por isso os dois alvos agora fazem `set -a; . ./.env; set +a` antes
de subir o processo.

Três instrumentações, cada uma sem captura de conteúdo sensível:
- **FastAPI** (`instrument_fastapi_app`): só método HTTP, rota e
  status code — nunca corpo da requisição/resposta.
- **SQLAlchemy** (`instrument_sqlalchemy_engine`, chamado em
  `adapters/postgres/engine.py:get_engine`, uma vez por instância de
  engine — não globalmente, porque testes recriam o engine cacheado
  via `get_engine.cache_clear()`): `db.statement` é o SQL
  parametrizado (bind parameters), nunca o valor literal.
- **Celery** (`CeleryInstrumentor`, instrumentado tanto no processo
  da API quanto no do worker — é o que propaga o contexto de trace
  através da mensagem do broker Redis, correlacionando o span do
  upload com o da task de indexação): só nome da task e ID do job —
  nunca `args`/`kwargs`. Nenhuma task atual recebe texto de documento
  como argumento de qualquer forma (`process_index_job_task` só
  recebe um UUID).

Testes (`tests/unit/test_tracing.py`) dublam os três limites com
efeito colateral real (exportador, processor, provider, os dois
instrumentors de terceiros) e verificam só a lógica deste módulo:
liga/desliga por `OTEL_TRACES_ENABLED`, idempotência da instrumentação
do Celery entre chamadas, o override por `OTEL_SERVICE_NAME`, e que
`instrument_fastapi_app`/`instrument_sqlalchemy_engine` delegam para
os instrumentors corretos. Validar exportação de verdade (traces
chegando no Collector, correlacionados entre API e worker) fica para
verificação manual via `docker compose up` — nenhum teste de pull
request sobe infraestrutura real (seção 1 do plano).

## Busca vetorial e provisionamento do gateway de embeddings (RAG-030)


Destrava a decisão de produto deixada propositalmente em aberto no
RAG-025/RAG-011 (ver seções acima): qual modelo de embeddings vive por
trás do alias `embedding-model-alias`, e portanto qual dimensão fixar
em `chunks.embedding` para poder criar um índice ANN. Decisão tomada:
**Qwen3-Embedding-0.6B**, self-hospedado via **Ollama**, dimensão
nativa **1.024** — abordagem open source, sem depender de um provedor
pago de embeddings.

**Por que Ollama, e não Text Embeddings Inference (TEI)**: TEI é a
opção "padrão" da Hugging Face para servir modelos de embedding, mas
tem um histórico documentado de falhas rodando o Qwen3-Embedding-0.6B
especificamente em CPU (crash por falta de export ONNX do modelo e
erros de Intel MKL — `huggingface/text-embeddings-inference#667`).
Ollama usa `llama.cpp` (formato GGUF) como motor de inferência, maduro
e primariamente desenhado para CPU, evitando essa classe de problema.

**Migration 0006** corrige `chunks.embedding` (criada sem dimensão em
0002 — RAG-011) para `vector(1024)` via `ALTER COLUMN ... USING
embedding::vector(1024)`, e cria o índice **HNSW** (`vector_cosine_ops`)
— HNSW em vez de ivfflat porque não exige um passo de treino com dados
já existentes (a tabela está vazia neste ponto) e é a recomendação
atual do próprio pgvector para a maioria dos casos.

`packages/application/ports/vector_search.py` define `VectorSearchPort`
(`search(tenant_id, knowledge_base_id, query_embedding, limit) ->
list[ScoredChunk]`) — reusa `ScoredChunk` de `lexical_search.py`
(RAG-031), já pensado desde então para os dois tipos de busca.
`adapters/vector_search/postgres.py` (`PostgresVectorSearch`) implementa
via `ChunkModel.embedding.cosine_distance(...)`, compilado pelo
SQLAlchemy para o operador `<=>` que o planner do Postgres casa com o
índice HNSW. Mesmos três critérios de aceite de RAG-031, pelo mesmo
padrão (filtros no `WHERE`, join até `documents.active_version_id`
para só a versão ativa, `ORDER BY` com `chunks.id` como desempate
determinístico) — com um filtro adicional, `embedding IS NOT NULL`
(um chunk cuja indexação ainda não gerou embedding nunca aparece num
resultado). O score devolvido é similaridade de cosseno (`1 -
distância`), não a distância bruta, para manter a mesma convenção
"maior é melhor" da busca lexical — RAG-032 (fusão RRF) é quem vai
combinar os dois rankings.

`adapters/vector_search/in_memory.py` (`InMemoryVectorSearch`) é um
fake para testar só o contrato da porta, mesmo papel do fake lexical —
calcula similaridade de cosseno em Python puro (sem numpy).

**Infraestrutura local** (`docker-compose.yml`): dois serviços novos.
`ollama` serve o modelo (porta `11434`); `ollama-pull-embedding-model`
roda uma vez, baixa `qwen3-embedding:0.6b` (~640MB) no volume
`ollama_data` e termina — subidas seguintes reaproveitam o modelo já
local. `litellm` (o proxy real, finalmente provisionado — RAG-025)
usa `config/litellm/config.yaml` para mapear o alias
`embedding-model-alias` para `ollama/qwen3-embedding:0.6b`; só sobe
depois que o pull termina com sucesso. Trocar de modelo no futuro é
editar esse `config.yaml` (e rodar `ollama pull` do novo modelo) —
nunca `config/models/embedding.v1.yaml`, cujo alias é estável (seção 8
do plano); uma mudança que exija um alias novo (dimensão diferente)
cria `embedding.v2.yaml`.

Testes: `tests/unit/test_vector_search_in_memory.py` cobre o contrato
da porta (ranking por similaridade, desempate determinístico, filtro
por tenant/base, `limit`, chunk sem embedding é ignorado,
`query_embedding` vazio levanta `ValueError`).
`tests/unit/test_schema.py` verifica a partir de `Base.metadata` (sem
banco real, mesma abordagem do RAG-011/031) que `chunks.embedding` tem
dimensão fixa 1.024 e que o índice HNSW existe com o operador
`vector_cosine_ops` correto. "Usa índice pgvector" de fato (via
`EXPLAIN` contra um Postgres real) e a integração ponta a ponta com
Ollama/LiteLLM ficam para verificação manual via `docker compose up` e
para `tests/integration/` quando essa suíte existir — mesma limitação
já documentada para os demais adapters Postgres deste projeto.

## Métricas Prometheus (RAG-053)


Destrava com métricas o mesmo que RAG-052 destravou com traces: técnica
(HTTP, Celery) e de consumo (negócio) — via OpenTelemetry, reaproveitando
a infraestrutura de RAG-003 (Collector expondo `:8889` em formato
Prometheus, já scrapado pelo Prometheus do `docker-compose.yml`).

`packages/observability/metrics.py` segue exatamente a mesma arquitetura
de `tracing.py` (RAG-052) — mesmo racional de ler `OTEL_*` diretamente
via `os.getenv`, mesmo interruptor próprio (`OTEL_METRICS_ENABLED`,
default `false`) para não exportar nada em teste, mesma garantia de
zero overhead quando desligado (a API do OpenTelemetry nunca falha sem
um `MeterProvider` real — devolve um meter "no-op" atrás de um proxy que
é atualizado sozinho se um provider real for definido depois).

**Técnicas**: nenhum código novo — `FastAPIInstrumentor`/`CeleryInstrumentor`
(já aplicados por `configure_tracing`/`instrument_fastapi_app`, RAG-052)
emitem métricas E traces ao mesmo tempo, a partir do que estiver
configurado globalmente no momento em que são chamados; por isso
`configure_metrics()` roda antes de `instrument_fastapi_app(app)` em
`apps/api/main.py` (mesma ordem que já valia para `configure_tracing`).

**De consumo**: `record_document_uploaded`/`record_document_reindexed`/
`record_knowledge_base_mutation`/`record_index_job_attempt`/
`record_embedding_batch` — chamados a partir dos MESMOS pontos de
entrada que já registram auditoria (RAG-054) ou processam o trabalho de
fato (routers da API, a task Celery, o adapter LiteLLM), nunca de
dentro de `packages/application` (domínio e casos de uso não importam
OpenTelemetry diretamente, seção 5.1 do plano — a mesma razão pela qual
RAG-054 registrou auditoria a partir dos routers). A única mudança em
`packages/application`: `process_index_job_attempt` (RAG-022) agora
devolve um `IndexJobAttemptOutcome | None` (antes devolvia sempre
`None`) — o único jeito de a task Celery distinguir sucesso de falha
definitiva sem inspecionar `IndexJob` de novo, já que as duas retornam
normalmente (só a falha NÃO definitiva levanta `RetryableIndexJobError`).

**Cardinalidade** (critério de aceite "labels não possuem cardinalidade
descontrolada"): todo label vem de um conjunto fixo e pequeno —
`mime_type` (4 valores, `_ALLOWED_EXTENSIONS_BY_MIME_TYPE`), `action`
(create/update/delete), `status` (succeeded/failed_retryable/failed_final)
— nunca `tenant_id`, `document_id` nem qualquer outro identificador de
cardinalidade livre.

**Dashboards básicos** (critério de aceite): `deploy/observability/grafana/
dashboards/rag-platform-overview.json`, carregado automaticamente pelo
provider `file` em `provisioning/dashboards/dashboards.yml` — seis
painéis (requisições HTTP por rota, documentos enviados por tipo MIME,
mutações de base de conhecimento por ação, tentativas de indexação por
desfecho, duração de indexação p50/p95, chamadas ao gateway de
embeddings). Os nomes exatos de métrica no Prometheus dependem da
conversão que o exporter do OTel Collector faz (pontos viram
underscores, contadores ganham sufixo `_total`, histogramas viram
`_bucket`/`_sum`/`_count`) — confirmar contra `curl localhost:8889/metrics`
na primeira subida real (`docker compose up`) fica para verificação
manual, mesma limitação já documentada para os demais adapters/infra
deste projeto que dependem de um Postgres/Collector real (nenhum teste
de pull request sobe infraestrutura real, seção 1 do plano).

Testes: `tests/unit/test_metrics.py` cobre `configure_metrics`
(ligado/desligado, override de `OTEL_SERVICE_NAME`) e cada `record_*`
(instrumento e labels corretos), tudo com o exportador/provider/reader
dublados — mesmo padrão de `test_tracing.py`. `test_index_job_processing.py`
cobre o novo valor de retorno de `process_index_job_attempt`.
`test_indexing_worker_task.py` cobre que a task registra o desfecho
certo (incluindo que a métrica de falha retryable é registrada e a
exceção ainda é relançada, para o `autoretry_for` do Celery continuar
funcionando). `test_knowledge_base_router.py`/`test_document_router.py`
cobrem que cada endpoint chama a métrica certa.

## Publicação de imagens no GHCR (RAG-072)


`.github/workflows/publish.yml` constrói e publica as imagens da API e
do worker em `ghcr.io` a cada push em `master` — na prática, a cada PR
mergeada (nunca em PR: publicar uma imagem de um branch de feature não
teria consumidor). Convive com `pull-request.yml`/`security.yml`
(RAG-070/071), que continuam sendo o gate de qualidade/segurança antes
do merge.

**`Dockerfile.api`/`Dockerfile.worker`** (multi-stage): o estágio
`builder` instala só as dependências de produção declaradas em
`pyproject.toml` (nunca os extras de dev — ruff/mypy/pytest/bandit/
pip-audit não têm por que existir na imagem publicada), usando pacotes
Python vazios como placeholder só para o `pip install .` resolver os
metadados do projeto sem precisar do código-fonte real ainda — isso
mantém a camada de instalação de dependências cacheável independente
de mudança de código. O estágio final copia o resultado desse install
mais `apps/`, `packages/`, `adapters/` e `config/` de verdade, roda
como usuário não-root, e usa `python -m uvicorn`/`python -m celery`
(em vez do executável direto) para garantir que o diretório de
trabalho (`/app`, onde `apps/`/`packages/`/`adapters/`/`config/` foram
copiados) entre no `sys.path` — sem isso, `apps.api.main` não seria
importável. **`config/` precisa ficar como irmão de `packages/` no
filesystem da imagem, nunca instalado via pip**: `packages/config/
models.py`/`packages/generation/prompts.py` resolvem o caminho via
`Path(__file__).parent.parent.parent / "config"`, relativo à raiz do
projeto, não a um pacote instalado (mesma razão por que `make install`
usa `pip install -e` em vez de um install normal).

**Critérios de aceite:**

- **"tag por SHA"**: cada imagem é publicada como `ghcr.io/<owner>/
  rag-platform-{api,worker}:sha-<sha completo do commit>` — imutável e
  rastreável a um commit exato. `:latest` também é publicada, como
  conveniência para quem só quer "a mais recente"; RAG-074/075 (deploy)
  devem sempre referenciar a tag `sha-<sha>` (ou o digest), nunca
  `:latest`, que é uma tag móvel.
- **"digest registrado"**: o digest (`sha256:...`) de cada imagem
  publicada é escrito no resumo da run do workflow
  (`GITHUB_STEP_SUMMARY`) — visível a partir da própria run, sem
  precisar reconstruir a imagem para descobrir qual digest foi
  publicado.
- **"SBOM gerada"**: `docker/build-push-action` (`sbom: true`) gera um
  SBOM (SPDX, via BuildKit) e o anexa à imagem publicada como
  attestation OCI — nenhuma ferramenta além do próprio buildx.
  `provenance: true` também é gerado (registro verificável de como/onde
  a imagem foi construída) — não é um critério de aceite explícito,
  mas é o mesmo mecanismo do buildx, sem custo adicional.
- **"nenhuma credencial permanente necessária"**: autenticação no GHCR
  usa o `GITHUB_TOKEN` efêmero da própria Actions run (`permissions:
  packages: write`, expira ao fim do job) — nenhum PAT nem segredo de
  longa duração é criado ou armazenado no repositório.

**Smoke check antes de publicar**: como não há Docker disponível no
ambiente onde esta atividade foi desenvolvida (nenhuma forma de
validar o build localmente antes do PR), o workflow constrói cada
imagem localmente na runner primeiro (`load: true`, sem publicar) e
roda um `python -c "import ..."` do módulo de entrada de cada uma
(`apps.api.main`/`apps.indexing_worker.worker`) dentro do container —
pega um Dockerfile quebrado (dependência faltando, caminho de `config/`
errado) antes de chegar ao registro. O cache do buildx (`type=gha`) faz
o build de publicação reaproveitar as camadas do build de teste, em
vez de reconstruir do zero.

**Validado localmente nesta atividade** (sem Docker disponível, mas
com as mesmas ferramentas que os jobs de CI usam): `hadolint --config
.hadolint.yaml --failure-threshold error` em ambos os Dockerfiles
(limpo — só um aviso `DL3008`, nível `warning`, não bloqueia) e
`actionlint` no workflow novo (limpo). A validação de que os builds
de fato funcionam (o smoke check acima) só acontece na primeira run
real do workflow, após o merge — mesma limitação já documentada para
os demais adapters/infra deste projeto que dependem de um ambiente
real (Postgres, Collector) que este sandbox não tem.
## Reranker (RAG-033)


Reordena os candidatos que já saíram da fusão RRF (RAG-032) por
relevância de verdade em relação à query, via um cross-encoder —
mais caro e mais preciso que o ranking por similaridade/RRF que já
os trouxe até ali. É o último passo de recuperação antes do endpoint
`retrieve` (RAG-034, ainda não implementado) e do context builder
(RAG-041).

**Porta e adapters configuráveis** (critério de aceite "pode ser
desativado"): `packages/application/ports/reranker.py` define
`RerankerPort` — domínio e casos de uso não importam LiteLLM (nem
qualquer cliente HTTP) diretamente, mesma disciplina hexagonal de
todo o projeto (seção 5.1 do plano). "Desativado" é a configuração de
QUAL adapter é injetado, nunca um `if` dentro de um adapter só:
`LiteLLMReranker` (`adapters/reranker/litellm.py`, reranking real via
o gateway LiteLLM) e `PassthroughReranker`
(`adapters/reranker/passthrough.py`, devolve os candidatos na mesma
ordem, truncados a `top_n`) implementam a mesma porta — quem chama
nunca sabe qual dos dois está por trás. A escolha é
`Settings.reranker_enabled` (`RERANKER_ENABLED`, padrão `false`) — cabe
a quem monta o endpoint `retrieve` (RAG-034) escolher o adapter a
partir dela; esta atividade entrega a porta e os dois adapters — o ponto de
injeção é `apps/api/routers/retrieval.py::get_reranker` (RAG-034).

**`LiteLLMReranker`** fala com o mesmo gateway LiteLLM de RAG-025/030
(`POST {base_url}/rerank`), no formato Cohere Rerank v2 que o LiteLLM
segue para qualquer provedor por trás (Cohere, Voyage, ou um reranker
self-hospedado): `{"model": alias, "query": ..., "documents": [...],
"top_n": ...}` → `{"results": [{"index": int, "relevance_score":
float}, ...]}`. Reaproveita as MESMAS configurações de timeout/retry
do gateway de embeddings (`Settings.litellm_*`) — é o mesmo proxy
LiteLLM, só um alias/endpoint diferente; mesma classificação de erro
de `adapters/litellm/embedding_provider.py` (HTTP 4xx nunca é
retentado; HTTP >= 500/erro de conexão/resposta malformada esgotam as
tentativas antes de levantar `RerankerUnavailableError`). O alias
(`config/models/reranker.v1.yaml`, `reranker-model-alias`) segue a
mesma convenção de `embedding.v1.yaml` (RAG-025/030): qual modelo real
fica atrás dele é decisão de configuração do gateway LiteLLM
(`config/litellm/config.yaml`), não deste arquivo. Este ticket não
escolhe nem provisiona esse modelo real — a atividade não pede essa
decisão (ao contrário de RAG-030, cujo critério de aceite exigia
decidir o modelo de embeddings).

**"timeout usa ranking anterior"** (critério de aceite):
`rerank_safely()` (mesmo padrão de `record_audit_event_safely`,
RAG-054) envolve `RerankerPort.rerank(...)` e devolve os candidatos
ORIGINAIS (truncados a `top_n`, na ordem de entrada — o "ranking
anterior" já produzido pela fusão RRF) em qualquer `RerankerError`
(timeout ou qualquer outro erro do gateway), nunca propagando a
exceção. Reranking é uma melhoria de qualidade sobre um ranking que já
é bom o suficiente — nunca deve derrubar uma consulta inteira.

**"registra latência sem registrar texto sensível"** (critério de
aceite): `LiteLLMReranker` mede a duração da chamada e registra via
`packages.observability.metrics.record_reranker_call` (RAG-053, só um
histograma, sem labels) — nunca o texto dos chunks nem a query, que
nunca viram atributo de métrica nem de log.

Testes: `tests/unit/test_reranker.py` cobre `rerank_safely` (sucesso
repassado verbatim; fallback para o ranking anterior em timeout e em
erro de indisponibilidade; truncamento a `top_n` no fallback).
`tests/unit/test_passthrough_reranker.py` cobre `PassthroughReranker`
(ordem preservada, truncamento, lista vazia). `tests/unit/
test_litellm_reranker.py` cobre `LiteLLMReranker` — mesma estrutura de
`test_litellm_embedding_provider.py`, todo o transporte HTTP dublado
via `httpx.MockTransport`: alias/query/documentos enviados
corretamente, reordenação por `relevance_score` decrescente,
truncamento a `top_n`, retry em erro transitório, esgotamento de
tentativas (timeout e indisponibilidade), erro 4xx nunca retentado,
resposta malformada, header `Authorization` quando `LITELLM_API_KEY`
configurado, e a métrica de latência (chamada só quando há
candidatos). `tests/unit/test_model_config.py` cobre o alias
`reranker.v1.yaml`.

## Endpoint retrieve (RAG-034)


Primeiro endpoint HTTP da fase de geração (E4 do plano): expõe busca
vetorial + busca lexical + fusão RRF (RAG-032) + reranking
configurável (RAG-033) como uma única chamada síncrona, `POST
/v1/knowledge-bases/{id}/retrieve`. Sem geração de resposta (isso é
RAG-042/043) e sem persistir `QueryLog`/`QueryEvidence` (RAG-010) —
essa persistência exige um `query_id` que só existe quando uma query
de verdade é registrada, o que fica para RAG-044. Este ticket entrega
só a recuperação, para poder validar a qualidade do retrieval
isoladamente (inclusive pelo dataset dourado de RAG-060) antes de
acoplar geração.

**Fluxo** (`packages/application/queries/retrieval.py::retrieve_evidence`):
busca a base de conhecimento pelo par `(tenant_id, knowledge_base_id)`
— 404 (nunca 403) se não existir ou for de outro tenant, mesmo padrão
de `documents.py`/`knowledge_bases.py` (RAG-012/RAG-051) — embeda a
query (`EmbeddingProviderPort`), dispara busca vetorial e lexical em
paralelo (`asyncio.gather`, cada uma trazendo um pool de até
`CANDIDATE_POOL_SIZE=100` candidatos), funde os dois rankings via
`reciprocal_rank_fusion` (RAG-032) e só então aplica reranking
(`rerank_safely`, RAG-033) sobre o resultado fundido, truncando a
`top_k`.

**Filtros (`page`/`section`) aplicados pós-fusão, em Python** —
decisão de escopo deliberada: em vez de empurrar o filtro para dentro
do SQL de `VectorSearchPort`/`LexicalSearchPort` (o que exigiria
alterar contratos de porta já mesclados em RAG-030/031), o filtro é
aplicado sobre os candidatos já buscados, antes do reranking. Isso é
suficiente para o volume atual e mantém as portas estáveis; se no
futuro um filtro muito seletivo em uma base grande esvaziar demais o
pool de 100 candidatos, vale revisitar e mover o filtro para a
consulta SQL — não é um problema deste ticket.

**Contrato bloqueia filtro arbitrário** (critério de aceite):
`RetrievalFiltersRequest` (`packages/contracts/retrieval.py`) usa
`extra="forbid"` (mesmo padrão de `KnowledgeBaseCreateRequest`) — só
`page`/`section` são aceitos; qualquer outra chave (ex.: `{"author":
...}`) gera 422 automaticamente via validação do Pydantic, sem
precisar de nenhuma lista de bloqueio própria. `top_k` é limitado por
`Field(ge=1, le=MAX_TOP_K)` no contrato E reclampado de novo dentro do
caso de uso (`bounded_top_k = max(1, min(top_k, MAX_TOP_K))`) — mesma
defesa em profundidade de `list_knowledge_bases`, já que
`packages/application` nunca importa `packages/contracts`.

**`rerank_score` é `None` quando o reranker está desativado**
(`RERANKER_ENABLED=false`): o score de fusão RRF nunca é reaproveitado
como se fosse um score de reranking de verdade — são métricas
diferentes, e expor isso errado no contrato enganaria quem consome a
API.

Testes: `tests/unit/test_retrieval_query.py` cobre o caso de uso
(fusão RRF entre vetorial+lexical, deduplicação de um chunk presente
nos dois rankings, filtro por `page`, filtro por `section`, respeita
`top_k`, reclampa `top_k` acima do máximo, posições 0-indexadas e
sequenciais, `rerank_score` ausente quando desativado, score/ordem
refletindo o reranker quando ativado, base inexistente ou de outro
tenant). `tests/unit/test_retrieval_router.py` cobre a visão HTTP
(200 com evidências/metadados/scores completos, 401 sem
`Authorization`, 404 para base inexistente ou de outro tenant, 422
para query vazia, 422 para `top_k` acima do máximo, 200 com filtro
`page` permitido, 422 com filtro arbitrário, seleção de
`PassthroughReranker`/`LiteLLMReranker` conforme
`Settings.reranker_enabled`) — mesmo padrão de app real +
`dependency_overrides` de `test_knowledge_base_router.py`, incluindo a
sobrescrita de `get_audit_log` (o endpoint `POST /v1/knowledge-bases`,
usado para criar a base de conhecimento de cada teste, registra
auditoria por baixo dos panos — esquecer essa sobrescrita faz o teste
tocar o `get_session`/`get_settings()` reais).

## Context builder (RAG-041)


Monta o texto de `CONTEXTO` do prompt de resposta (RAG-040) a partir
das evidências que o endpoint `retrieve` (RAG-034) já buscou, fundiu
por RRF (RAG-032) e reordenou pelo reranker (RAG-033) — o passo 10 da
seção 12 do plano ("montar contexto dentro do orçamento de tokens"),
o último antes de chamar o modelo (RAG-042).

**Seleção em ordem de `position`, respeitando o orçamento**
(`packages/generation/context_builder.py::build_context`): percorre as
evidências da melhor posição para a pior (`position` crescente — `0`
é o melhor candidato; a lista de entrada não precisa já vir ordenada
assim, mesma postura defensiva de `retrieve_evidence` reclampando
`top_k`) e inclui cada uma enquanto ela couber no `token_budget`
restante — usa `Chunk.token_count`, já calculado na indexação
(RAG-024), nunca retokeniza o texto aqui. Uma evidência grande demais
para o orçamento restante é só pulada, nunca trunca o conteúdo do
chunk para caber: cortar um chunk no meio produziria uma citação
`[chunk_id]` referenciando um texto que o chunk recuperado, de
verdade, não contém por inteiro — exatamente o que RAG-043 ("toda
citação corresponde a chunk recuperado") existe para impedir. Uma
evidência maior que o orçamento não bloqueia uma evidência menor e
pior posicionada logo depois dela na lista.

**"evita duplicações excessivas"** (critério de aceite): evidências
com `chunk.content` idêntico a uma já incluída são descartadas —
comparação exata, não uma heurística de similaridade (RRF, RAG-032,
já deduplica o mesmo `chunk_id` repetido nos dois rankings de entrada;
esta atividade só cobre o caso de dois `chunk_id` diferentes com o
mesmo texto, por exemplo o mesmo trecho reindexado em duas versões do
documento).

**Formato de citação compatível com RAG-040**: o texto de contexto usa
exatamente `[chunk_id] conteúdo`, o formato que `citation_instruction`
(`config/prompts/answer.v1.yaml`) pede ao modelo para citar — um dos
testes monta o contexto e chama `PromptTemplate.render()` de verdade
para travar os dois módulos não divergirem silenciosamente. Não há
"orçamento padrão de produção" decidido aqui: `token_budget` é sempre
um parâmetro explícito de quem chama, porque o valor certo depende da
janela de contexto do modelo que RAG-042 ainda vai escolher —
`DEFAULT_TOKEN_BUDGET` existe só para testes e chamadas exploratórias.

**Não decide "não há evidência suficiente"**: o limiar mínimo do passo
9 da seção 12 e a resposta fixa de `no_evidence_response` (seção 12.1)
não são responsabilidade deste módulo — aqui, nenhuma evidência
couber no orçamento é uma saída legítima e silenciosa (`context_text`
vazio); decidir o que fazer com um contexto vazio fica para quem
monta o endpoint `query` (RAG-043/044).

Testes: `tests/unit/test_context_builder.py` cobre lista vazia,
evidência que cabe, evidência que estoura o orçamento, evidência
grande demais não bloqueando uma menor depois dela, orçamento zero,
deduplicação de conteúdo idêntico, ordenação por `position`
independente da ordem de entrada, múltiplas evidências preservando
ordem no texto final, e a integração de formato com
`PromptTemplate.render()` (RAG-040).
## Dataset dourado de avaliação (RAG-060)

Schema versionado de perguntas, respostas esperadas e evidências
esperadas — a referência fixa contra a qual RAG-061 (Recall@K, MRR) e
RAG-062 (faithfulness, answer relevancy) vão medir a qualidade do RAG.
Sem essa referência, "melhorou" ou "piorou" não tem como ser medido;
`EvaluationRun.dataset_version` (seção 9 do plano) associa toda
execução de avaliação à versão exata do dataset contra a qual ela
rodou.

**Mesma convenção de versionamento imutável do prompt de resposta**
(RAG-040): `datasets/golden/<id>.<version>.yaml`, carregado por
`packages/evaluation/golden_dataset.py::load_golden_dataset(id,
version)` — uma versão publicada nunca é editada, uma mudança de
conteúdo sempre cria uma versão nova. `get_default_golden_dataset()` é
o único lugar que decide qual versão a avaliação usa hoje (`golden`,
`v1`).

**Por que `expected_evidence` não referencia `chunk_id`**: um
`chunk_id` é um UUID gerado no momento da indexação — reindexar os
mesmos documentos, ou rodar em outro ambiente, gera UUIDs diferentes,
então não serviria como identificador estável num dataset versionado
no repositório. Em vez disso, cada evidência esperada aponta para um
`document_id` (um identificador estável escolhido por quem cura o
dataset, nunca um ID de banco) e um `content_contains` — um trecho de
texto que RAG-061 vai usar para casar um chunk recuperado de verdade
com a expectativa do caso, por conteúdo.

**Invariantes do schema, não só convenção de quem escreve o caso**
(`GoldenCase`/`GoldenDataset`, `pydantic.model_validator`): uma
pergunta sem resposta (`expected_answer=None` — critério de aceite
"inclui perguntas sem resposta") não pode ter `expected_evidence` (não
faria sentido ter evidência para uma pergunta que não tem resposta);
uma pergunta com resposta esperada precisa de ao menos uma evidência;
o dataset inteiro precisa ter pelo menos `MINIMUM_CASE_COUNT` (30)
casos (critério de aceite, também item da Definition of Done da POC,
seção 20 do plano) e pelo menos uma pergunta sem resposta; IDs de caso
não podem se repetir. Um dataset que viole qualquer uma dessas regras
falha ao carregar, antes de qualquer avaliação rodar contra ele.

**Corpus de referência do `golden.v1.yaml`: o próprio README.md deste
repositório** — 40 casos (35 respondíveis + 5 sem resposta, acima do
mínimo de 30), cada um apontando para uma seção real deste arquivo.
Decisão deliberada desta atividade: um corpus real, estável e já
versionado no repositório, sem depender de nenhuma infraestrutura viva
(Postgres, embeddings) só para o dataset existir — ingerir um corpus
de documentos de verdade numa base de conhecimento indexada é
trabalho de integração, não de definição de schema, e fica para
RAG-061, quando a avaliação de retrieval de fato rodar contra uma base
indexada (o próprio README.md é um candidato natural de corpus para
isso, já que este dataset já referencia suas seções). Os 5 casos sem
resposta perguntam sobre algo deliberadamente fora do que o README
documenta (SLA contratual, custo, aplicativo mobile, um valor de
faithfulness medido em produção — não a meta da POC, um DPO) — para
exercitar o comportamento "não há evidência suficiente" (RAG-040/043),
nunca uma resposta inventada.

Testes: `tests/unit/test_golden_dataset.py` cobre o carregamento real
de `golden.v1` (id/versão corretos, mínimo de casos, pelo menos uma
pergunta sem resposta, IDs únicos, toda pergunta respondível tem
evidência), erro (`GoldenDatasetNotFoundError`) para id/versão
inexistente, erro de inconsistência id/versão-vs-nome-de-arquivo,
cache por `(id, version)` e imutabilidade — e, construindo
`GoldenCase`/`GoldenDataset` diretamente, cada invariante em isolado
(pergunta sem resposta com evidência, pergunta respondível sem
evidência, poucos casos, sem nenhuma pergunta sem resposta, IDs
duplicados, campo desconhecido rejeitado por `extra="forbid"`).

## Geração via LiteLLM (RAG-042)

Porta e adapter de chat completion — o passo "gerar resposta com LLM"
da seção 12 do plano. `packages/application/ports/generation_provider.py`
define `GenerationProviderPort.generate(*, prompt: str) -> GenerationResult`;
`adapters/litellm/generation_provider.py` (`LiteLLMGenerationProvider`)
implementa contra o mesmo gateway LiteLLM de RAG-025/030/033
(`POST {base_url}/chat/completions`, formato compatível com OpenAI),
via `httpx` puro — mesmo racional de "por que não a SDK `litellm`" já
documentado no adapter de embeddings.

**Prompt como uma única mensagem, não uma lista de mensagens
estruturada**: `PromptTemplate.render()` (RAG-040) já concatena
sistema + aviso de conteúdo não confiável + contexto + instrução de
citação + pergunta numa única string. Este adapter não inventa uma
divisão paralela em mensagens de sistema/usuário — o texto inteiro
vira o conteúdo de uma única mensagem `"role": "user"`.

**"usa alias"**: mesmo padrão de RAG-025/033 —
`packages/config/models.py::get_default_generation_model()`, alias
carregado de `config/models/generation.v1.yaml`, nunca hardcoded.

**Timeout e retry**: reaproveitam `Settings.litellm_timeout_seconds`/
`litellm_max_retries` — o mesmo proxy dos outros dois adapters, sem
configuração separada. Mesma classificação de erro: HTTP 4xx nunca é
retentado; timeout/HTTP >= 500/erro de conexão/corpo malformado
esgotam as tentativas antes de levantar `GenerationTimeoutError`/
`GenerationUnavailableError`.

**"aplica fallback configurável"**: decisão deliberada desta
atividade, porque o plano não elabora o que "fallback" significa para
geração especificamente (só há precedente para reranking, RAG-033, que
tem um estado anterior óbvio para reverter — o ranking RRF que já
existia antes do reranking ser tentado). Para geração não existe
"resposta anterior" nenhuma — "falhar de volta para nada" não é uma
opção. A interpretação adotada: um SEGUNDO alias de modelo
(`config/models/generation-fallback.v1.yaml`, carregado por
`get_default_generation_fallback_model()`), ligado por
`Settings.generation_fallback_enabled` (default `False`, mesmo padrão
liga/desliga de `Settings.reranker_enabled`). Quando ligado, esgotar as
tentativas no alias principal não levanta a exceção — o mesmo laço de
retry roda de novo contra o alias de fallback; só se esse segundo laço
também esgotar é que a exceção (do fallback) propaga. Quando
desligado, esgotar o alias principal já levanta a exceção na hora, e o
arquivo de configuração do fallback nem precisa existir (só é
resolvido sob demanda, na primeira vez que é de fato necessário).

**"registra uso"**: `GenerationResult` devolve `prompt_tokens`,
`completion_tokens`, `total_tokens` (repassados pelo gateway) e
`used_fallback` (qual alias respondeu) — RAG-044 vai persistir isso em
`QueryLog.token_usage` (coluna já existente desde RAG-010). O adapter
também emite uma métrica de consumo
(`packages/observability/metrics.py::record_generation_call`, RAG-053):
contadores de tokens de prompt/resposta e duração da chamada, rotulados
por `path` (`"primary"`/`"fallback"` — só 2 valores fixos, mesma
disciplina de cardinalidade das outras métricas de consumo) — nunca o
texto do prompt nem o da resposta.

Testes: `tests/unit/test_litellm_generation_provider.py` cobre alias
enviado, conteúdo da mensagem, uso de token devolvido, retry em erro
transitório, erro definitivo após esgotar tentativas (timeout, conexão,
servidor), erro imediato sem retry em HTTP 4xx, corpo malformado
(incluindo conteúdo que não é texto), header de autorização, métrica de
consumo — e, especificamente para o fallback: desligado nunca chama o
alias de contingência; ligado tenta o alias principal primeiro e só
então o de fallback; erro do fallback (não do principal) é o que
propaga quando os dois esgotam; métrica registra `used_fallback=True`
quando é o fallback que responde; o alias de fallback é resolvido uma
única vez mesmo em chamadas repetidas. `tests/unit/test_model_config.py`
ganhou os casos equivalentes de `generation`/`generation-fallback` já
cobertos para `embedding`/`reranker`.

## Avaliação de retrieval — Recall@K e MRR (RAG-061)

Mede a qualidade do retrieval (RAG-030/031/032/033) contra o dataset
dourado (RAG-060): Recall@K (fração das evidências esperadas de cada
caso encontrada entre os `K` chunks recuperados) e MRR — Mean
Reciprocal Rank (posição da primeira evidência relevante, invertida e
com média sobre os casos), seção 21 do plano ("Recall@5 inicial igual
ou superior a 0,80", "MRR inicial igual ou superior a 0,70").

**Correspondência por conteúdo, nunca por `chunk_id`**: `ExpectedEvidence`
(RAG-060) não referencia um `chunk_id` — um UUID gerado só na indexação,
que muda a cada reindexação/ambiente. `packages/evaluation/
retrieval_metrics.py` casa um chunk recuperado de verdade contra a
expectativa de um caso checando se `content_contains` aparece como
substring do conteúdo recuperado, mesmo critério já usado por RAG-060
para validar a consistência do próprio dataset contra o README.

**Só casos respondíveis**: `evaluate_retrieval`
(`packages/evaluation/retrieval_evaluation.py`) considera apenas os
casos com `expected_evidence` não vazio — uma "pergunta sem resposta"
(RAG-060) não tem evidência esperada para medir recall/MRR contra ela;
essas perguntas verificam a recusa de geração (RAG-043/RAG-062), não a
qualidade do retrieval. Levanta `EmptyEvaluationError` se nenhum caso
do dataset for respondível.

**Reusa `retrieve_evidence` (RAG-034) sem modificação** — a mesma
função que o endpoint `/retrieve` chama, para que a avaliação meça
exatamente o retrieval de produção (busca vetorial + lexical, fusão
RRF, reranking opcional), nunca um caminho de código paralelo só para
avaliação.

**Limiar configurável** (critério de aceite "falha por limiar
configurável"): `check_thresholds` recebe `minimum_recall_at_k`/
`minimum_mrr` de quem chama — nenhum valor de negócio é hardcoded
dentro de `retrieval_evaluation.py`; os defaults (0,80/0,70, seção 21
do plano) vivem só em `scripts/run_retrieval_evaluation.py`, como
flags de linha de comando.

**Relatório JSON e Markdown** (critério de aceite): `packages/
evaluation/retrieval_report.py` serializa `RetrievalEvaluationReport`
nos dois formatos — JSON com só tipos nativos (sem `UUID`/`datetime`
crus) para consumo por ferramenta (ex.: um passo de CI, RAG-073);
Markdown com o resumo agregado, o veredito do limiar e uma tabela por
caso, para leitura humana.

**Execução reproduzível, mas com um requisito deliberado**:
`scripts/run_retrieval_evaluation.py` chunka o mesmo README.md contra o
qual o dataset dourado foi curado (RAG-024, `chunk_document` com os
defaults de produção), indexa os chunks em `InMemoryVectorSearch`/
`InMemoryLexicalSearch` (sem tocar Postgres/MinIO) e roda
`evaluate_retrieval` contra eles. Sempre usa `LiteLLMEmbeddingProvider`
real (nunca um fake) — um número de Recall@K/MRR só significa algo com
embeddings de verdade (seção 21 do plano fixa metas concretas); isso
exige um gateway LiteLLM alcançável, então este script nunca roda como
parte de `pytest tests/unit` (seção 15 do plano: "chamadas reais ficam
em workflow manual ou agendado com orçamento limitado") — rodá-lo de
verdade fica para RAG-073 (quality gate de RAG no CI, ainda não
implementado) ou uma execução manual/agendada. Reranking fica
desativado por padrão (`--reranker passthrough`, sem depender de um
segundo gateway); `--reranker litellm` inclui reranking real.

Testes: `test_retrieval_metrics.py` (funções puras — 100% de
cobertura), `test_retrieval_evaluation.py` (`evaluate_retrieval`/
`check_thresholds`, com portas fake em memória e um provedor de
embeddings determinístico próprio do teste — vetores "one-hot"
ortogonais por tópico, dando controle total sobre qual chunk cada
pergunta recupera sem depender de nenhum modelo real), e
`test_retrieval_report.py` (JSON/Markdown). `scripts/
run_retrieval_evaluation.py` não tem teste direto — mesmo padrão já
estabelecido no projeto para o que exige infraestrutura/rede real
(adapters Postgres, RAG-044): mypy garante a corretude de tipos, a
execução de verdade é responsabilidade de RAG-073/manual.
## Validação de groundedness e citações (RAG-043)

Passo 12 do fluxo de consulta (seção 12 do plano): depois do modelo
responder (RAG-042), valida se a resposta pode ser confiada ao usuário
— `packages/generation/groundedness.py`. Escopo deliberadamente
restrito ao que o critério de aceite pede ("toda citação corresponde a
chunk recuperado; resposta inválida usa fallback seguro"):

- **Valida citações, não afirmações**: `extract_cited_chunk_ids()`
  reconhece um `[chunk_id]` (formato de `citation_instruction`,
  RAG-040) só quando o conteúdo entre colchetes é um UUID válido — um
  colchete que não é uma citação (nota, o que for) é ignorado, nunca
  tratado como citação inválida. `validate_groundedness()` então
  compara as citações reconhecidas contra
  `ContextBuildResult.included_evidence` (RAG-041, os chunks que de
  fato entraram no contexto que o modelo viu): qualquer citação fora
  desse conjunto é uma citação inventada. Verificar se o texto ao
  redor de uma citação está de fato sustentado pelo conteúdo do chunk
  citado (faithfulness, claim a claim) fica fora do escopo — isso é
  avaliação (RAG-062, com LLM-juiz e o dataset dourado RAG-060), não
  uma checagem barata de rodar em toda consulta em produção.
- **"Resposta sem suporte"** é qualquer resposta com ZERO citações
  válidas — nenhuma reconhecida, ou nenhuma que corresponda à evidência
  incluída. Uma citação malformada (UUID errado, formato inventado)
  nunca casa com nada, então cai no mesmo caminho: não abre uma brecha
  separada.
- **Caso especial**: a resposta idêntica (ignorando espaço nas pontas)
  a `no_evidence_response` (`config/prompts/answer.v1.yaml`, RAG-040) é
  sempre válida, mesmo sem nenhuma citação e mesmo que
  `included_evidence` não esteja vazio — o modelo pode legitimamente
  decidir que o contexto recebido não sustenta uma resposta. Sem esse
  caso especial o comportamento correto do modelo seria marcado como
  inválido só por não citar nada (inofensivo no texto final, já que o
  fallback devolveria a mesma frase, mas contaminaria qualquer
  auditoria/métrica de groundedness com falsos positivos).
- **Fallback seguro reaproveita `no_evidence_response`**, a mesma frase
  que já existe para "não há evidência suficiente" (passo 9, RAG-040) —
  em vez de inventar uma segunda mensagem. Do ponto de vista de quem
  pergunta, um threshold de recuperação insuficiente e uma citação
  inválida são o mesmo desfecho: "não há uma resposta confiável para
  dar".

`enforce_groundedness()` é a função que RAG-044 chama de fato: valida e
já devolve o texto final (`GroundednessOutcome.content` — a resposta
original quando válida, `no_evidence_response` quando não) junto com
`fallback_applied` e as citações (válidas/inválidas) reconhecidas, para
auditoria/observabilidade, sem precisar rodar a validação de novo.
`validate_groundedness()` fica exposta separadamente (só o diagnóstico,
sem decidir o texto final) para quem quiser inspecionar o resultado
sem aplicar o fallback.

Testes: `tests/unit/test_groundedness.py` (100% de cobertura) — todas
as combinações de citação (nenhuma, uma válida, uma inventada, mistura
válida/inventada, malformada, repetida, múltiplas distintas), o caso
especial de `no_evidence_response` (com e sem evidência incluída, com
espaço nas pontas) e `enforce_groundedness` aplicando ou não o
fallback.

## Endpoint de consulta com geração (RAG-044)

`POST /v1/knowledge-bases/{id}/query` (seção 10.3/10.5 do plano):
integra recuperação (RAG-034), montagem de contexto (RAG-041), geração
(RAG-042) e validação de groundedness (RAG-043), persistindo
`QueryLog`/`QueryEvidence` (RAG-010) — o par de RAG-034 ("expor
recuperação sem geração"), agora "com geração". O caso de uso
(`packages/application/commands/query.py::answer_query`) segue os
passos 9-14 da seção 12 do plano; os passos 1-8 são inteiramente
`retrieve_evidence` (RAG-034), reaproveitado sem duplicação.

**Passo 9 (limiar mínimo)**: `Settings.retrieval_minimum_score`
(default `0.0`) compara o score "efetivo" de cada evidência (o de
rerank quando reranking rodou de verdade, senão o de retrieval/RRF) —
os dois nunca estão na mesma escala (RRF fica perto de 0; rerank
costuma ser 0-1), então o default aceita qualquer evidência não vazia
sem exigir calibração prévia (mesmo espírito de `reranker_enabled=False`).
Sem nenhuma evidência acima do limiar — ou com o contexto montado
(RAG-041) saindo vazio mesmo depois de passar no limiar por score
individual — nenhuma chamada de geração acontece: a resposta já é
`no_evidence_response` (RAG-040), registrada com o rótulo
`NO_GENERATION_MODEL_LABEL` em `QueryLog.model` (nunca um alias de
modelo que na verdade não foi invocado).

**Passo 10 (orçamento de tokens)**: `Settings.
generation_context_token_budget` (default `3000`, igual ao default de
`context_builder.DEFAULT_TOKEN_BUDGET`) — o valor real depende da
janela de contexto do modelo por trás do alias de geração escolhido.

**Passo 11 (geração)**: alias resolvido pelo router
(`get_default_generation_model()`/`get_default_generation_fallback_model()`,
RAG-042) e passado como parâmetro explícito ao caso de uso — nenhum
código em `packages/application` importa `packages.config.models`
diretamente (mesma disciplina de decoupling da seção 5.1, aplicada à
configuração). Uma falha do gateway depois de esgotar tentativas
(`GenerationError`, RAG-042) vira `ServiceUnavailableError` (503, novo
em `packages/application/errors.py`) — uma indisponibilidade real de
infraestrutura, nunca confundida com "resposta sem suporte" (RAG-043,
que só se aplica a UMA resposta que o modelo de fato gerou).

**Passo 12 (groundedness)**: `enforce_groundedness` (RAG-043). Uma
resposta é `grounded` quando tem pelo menos uma citação válida e o
fallback não foi acionado — o próprio texto `no_evidence_response`
(gerado pelo modelo por conta própria) também não é `grounded` (zero
citações, mesmo sendo "válido" para RAG-043).

**Citações resolvidas com um novo método de porta**:
`DocumentRepositoryPort.get_documents_by_chunk_ids()` (novo, RAG-044) —
o contrato da seção 10.5 do plano pede `document_id`/`document_name`
por citação, e nenhuma atividade anterior precisou resolver
`Chunk.version_id` -> `Document` (RAG-034 devolve só o chunk).
Implementado tanto no fake em memória quanto no adapter Postgres (join
`chunks` -> `document_versions` -> `documents`); omite silenciosamente
um `chunk_id` sem documento resolvível, nunca levanta exceção. O
`excerpt` de cada citação corta o conteúdo do chunk em
`EXCERPT_MAX_CHARS` (300) caracteres — só para exibição, nunca afeta o
texto que o modelo recebeu como contexto nem a validação de
groundedness, que sempre usam o conteúdo completo.

**Persistência (`QueryRepositoryPort`, novo)**: `persist_query()` grava
`QueryLog` + todas as `QueryEvidence` juntas — sempre TODA a evidência
recuperada (RAG-034), não só a que entrou no contexto: RAG-061
(avaliação de retrieval) vai precisar do ranking completo,
independente de quantas evidências couberam no orçamento de tokens.
`question_hash` (SHA-256) nunca a pergunta em texto puro (seção 13 do
plano: "logs não devem guardar... perguntas sensíveis integralmente").
`trace_id` vem de `packages.observability.tracing.get_current_trace_id()`
(novo): converte o trace ID de 128 bits do span ativo (OpenTelemetry,
RAG-052) para `UUID` — fora de um span válido (tracing desligado), o
resultado é o UUID nulo, um valor previsível, nunca uma exceção.

Testes: `test_query_command.py` (caso de uso, com fakes em memória para
toda porta — 100% de cobertura), `test_query_router.py` (visão HTTP,
mesmo padrão de `test_retrieval_router.py`), extensões em
`test_document_repository_in_memory.py` (`get_documents_by_chunk_ids`),
novo `test_query_repository_in_memory.py`, e extensões em
`test_tracing.py` para `get_current_trace_id`. Adapters Postgres novos
(`adapters/query_repository/postgres.py`, o método novo de
`adapters/document_repository/postgres.py`) seguem o mesmo padrão já
estabelecido no projeto: sem teste direto (só integração, RAG-080,
fecharia essa lacuna) — mypy garante a corretude de tipos.

## Avaliação de geração — faithfulness e answer relevancy (RAG-062)

Mede a qualidade da geração (RAG-040/041/042/043/044) contra o dataset
dourado (RAG-060): faithfulness (toda alegação da resposta é sustentada
pelo contexto recuperado, sem alegação inventada) e answer relevancy (a
resposta de fato responde à pergunta feita) — seção 21 do plano:
"Faithfulness inicial igual ou superior a 0,85".

**Reusa `answer_query` (RAG-044) sem modificação** — a mesma função que
o endpoint `/query` chama — para que a avaliação meça exatamente a
geração de produção (recuperação + contexto + geração + validação de
groundedness), nunca um caminho de código paralelo só para avaliação;
mesmo racional de `evaluate_retrieval` (RAG-061) reusar
`retrieve_evidence` sem modificação.

**`QueryAnswer.context_chunk_contents` (extensão de RAG-044)**: o
conteúdo dos chunks que de fato entraram no contexto de geração
(`ContextBuildResult.included_evidence`) agora viaja no resultado do
caso de uso — existe só para que a avaliação julgue faithfulness contra
o MESMO contexto que o modelo recebeu, sem rodar a recuperação uma
segunda vez (o que dobraria o custo de rede de cada caso avaliado).
Nunca é serializado na resposta HTTP (`apps/api/routers/query.py`
monta `QueryResponse` campo a campo) — um detalhe interno de avaliação.

**Avaliação via LLM-juiz, atrás de uma porta própria**
(`GenerationEvaluatorPort`, `packages/application/ports/
generation_evaluator.py`) — seção 5 do plano, decisão arquitetural
"Ragas ou DeepEval atrás de interface": o QUE a porta expõe (dois
scores 0.0-1.0) nunca vaza qual biblioteca ou modelo está por trás. A
implementação real (`adapters/litellm/generation_evaluator.py`) usa o
mesmo gateway LiteLLM de RAG-025/030/033/042 com um prompt dedicado
(`config/prompts/generation-judge.v1.yaml`) que instrui o modelo a
devolver um JSON estrito `{"faithfulness": ..., "answer_relevancy":
...}` — este projeto evita dependências pesadas de avaliação sempre que
um adapter fino resolve (mesmo racional de não baixar o vocabulário do
`tiktoken`, `packages/ingestion/chunking.py`); nada impede uma
implementação futura atrás desta MESMA porta usar Ragas/DeepEval de
verdade.

**"modelo avaliador configurável"** (critério de aceite): alias PRÓPRIO
(`config/models/generation-evaluator.v1.yaml`,
`get_default_generation_evaluator_model()`), deliberadamente distinto
do alias de geração de resposta — o modelo que avalia não deveria ser o
mesmo que gerou, para reduzir viés de autoavaliação.

**"custos registrados"**: mesmo proxy de custo já estabelecido em toda
chamada de LLM do projeto (tokens consumidos, não uma tabela de preço
que este projeto não tem) — `record_generation_evaluation_call`
(`packages/observability/metrics.py`, novo) segue exatamente o padrão
de `record_generation_call`, sem label de fallback (um modelo-juiz não
tem alias de contingência).

**"resultados ligados às versões de prompt/modelo"**:
`GenerationEvaluationReport` registra o alias de geração, o (id,
versão) do prompt de resposta, e o alias do modelo-juiz usados numa
execução — os três valores que, juntos, dizem se um número de
faithfulness/relevancy é comparável ao de outra execução.

**Decisão de escopo**: `packages/evaluation/generation_evaluation.py`
define `ThresholdCheck`/`_mean` localmente em vez de importar de
`packages.evaluation.retrieval_evaluation` (RAG-061) — esta atividade
depende só de RAG-044/RAG-060 (RAG-061 é uma branch irmã, sem relação
de dependência declarada), então acoplar as duas branches só para
reusar ~10 linhas triviais custaria mais do que duplicá-las.

**Execução real fica fora do `pytest` padrão**, mesmo racional de
`scripts/run_retrieval_evaluation.py` (RAG-061): `scripts/
run_generation_evaluation.py` sempre usa os adapters LiteLLM reais
(embeddings, geração e avaliação), exigindo um gateway alcançável;
persiste o documento de origem via `InMemoryDocumentRepository` (não só
os chunks brutos em `VectorSearchPort`), já que `answer_query` resolve
citações via `DocumentRepositoryPort.get_documents_by_chunk_ids`.

Testes: `test_generation_evaluation.py` (orquestração, com fakes em
memória e um avaliador determinístico próprio do teste — 100% de
cobertura), `test_generation_report.py` (JSON/Markdown),
`test_litellm_generation_evaluator.py` (parsing do JSON de scores,
timeout/retry — mesmo padrão de `test_litellm_generation_provider.py`),
`test_judge_prompt.py` (carregador do prompt de avaliação, mesmo padrão
de `test_prompts.py`), extensões em `test_model_config.py`
(`generation-evaluator`) e `test_query_command.py`
(`context_chunk_contents`). `scripts/run_generation_evaluation.py` não
tem teste direto — mesmo padrão já estabelecido para o que exige
infraestrutura/rede real.
## Feedback (RAG-045)

Endpoint `POST /v1/feedback` (seção 10.3 do plano): registra a
avaliação do usuário (`Feedback`, seção 9 do plano; já modelado desde
RAG-010, tabela `feedbacks` já migrada em RAG-011/migração 0002 — esta
atividade não precisa de migração nova) sobre uma resposta já dada por
`POST .../query` (RAG-044).

**Standalone, não aninhado sob uma base de conhecimento**:
`/v1/feedback` recebe `query_id` no corpo — a base de conhecimento já
está implícita no `QueryLog` correspondente, então não há necessidade
de `knowledge_base_id` na URL nem no payload.

**`get_query_log`/`persist_feedback` vivem em `QueryRepositoryPort`**,
não em uma porta nova: `Feedback` é sempre subordinado a um `QueryLog`
já existente (FK `query_id`, `ondelete=CASCADE`), mesmo racional de
`DocumentRepositoryPort` reunir `Document`+`DocumentVersion`+`IndexJob`
numa porta só. `get_query_log` não filtra por tenant no nível da porta
(mesmo padrão de `DocumentRepositoryPort.get_document`) — quem decide
se o `tenant_id` do `QueryLog` encontrado corresponde ao do tenant
autenticado é o caso de uso.

**"respeita tenant; não permite feedback para query alheia"** (critério
de aceite): `submit_feedback` (`packages/application/commands/
feedback.py`) resolve `query_id` via `get_query_log` e levanta
`NotFoundError` (404) tanto para uma consulta inexistente quanto para
uma consulta de outro tenant — exatamente o mesmo erro nos dois casos,
mesma disciplina "404, nunca 403" de todo o resto da API
(RAG-012/RAG-021/RAG-034/RAG-044).

**"valida rating e motivo"** (critério de aceite), interpretado nesta
atividade como: `rating` só aceita os dois valores de `FeedbackRating`
— qualquer outra string já é 422 automaticamente pela validação de
enum do Pydantic, sem lógica extra necessária; `reason` é obrigatório
quando `rating == NEGATIVE` (`model_validator` em
`packages/contracts/feedback.py::FeedbackRequest`) — decisão desta
atividade, já que o plano não elabora o que "validar motivo" significa
concretamente, e um feedback negativo sem motivo não é acionável para
quem for revisar a resposta depois. Para `POSITIVE`, `reason` continua
opcional. Essa validação é de forma/contrato (422 na borda HTTP), não
uma regra de negócio do caso de uso — mesma disciplina já usada no
resto da API (validação de payload no contrato, regra de negócio no
caso de uso).

Testes: `test_feedback_command.py` (caso de uso, com fake em memória —
100% de cobertura), `test_feedback_router.py` (visão HTTP, mesmo padrão
de `test_query_router.py` — semeia um `QueryLog` diretamente via
`InMemoryQueryRepository.persist_query`, sem precisar rodar o pipeline
completo de consulta nem criar uma base de conhecimento), extensões em
`test_query_repository_in_memory.py` para `get_query_log`/
`persist_feedback`. O método novo de `adapters/query_repository/
postgres.py` segue o mesmo padrão já estabelecido no projeto: sem teste
direto (só integração, RAG-080, fecharia essa lacuna) — mypy garante a
corretude de tipos.

Com RAG-045, a épica E4 (Geração fundamentada) está completa.

## Baseline da POC e verificação de regressão (RAG-063)

Objetivo (seção 21 do plano, "Requisitos de desempenho iniciais"):
`Recall@5 >= 0,80`, `MRR >= 0,70`, `Faithfulness >= 0,85`, "Regressão
máxima permitida de 5% contra a baseline aprovada".

**Decisão de escopo — por que este módulo não importa nada de
RAG-061/RAG-062.** O plano lista "Dependências: RAG-061, RAG-062" para
esta atividade, mas as duas vivem em branches irmãs ainda não
mescladas (`feat/rag-061-retrieval-evaluation`, ramificada de
`master`, e `feat/rag-062-generation-evaluation`, empilhada sobre a
pilha de RAG-043/044) — nenhuma contém a outra; importar diretamente
`RetrievalEvaluationReport`/`GenerationEvaluationReport` exigiria
mesclar as três branches juntas antes de qualquer PR poder ser aberto
isoladamente (o mesmo problema de import cruzado já resolvido dentro
de RAG-062 propriamente, ver seção "Avaliação de geração" acima).
`packages/evaluation/baseline.py` resolve isso operando sobre
`dict[str, float]` genérico: exatamente o formato que
`report_to_dict()` de ambos os relatórios já produz como chaves de
nível superior (`recall_at_k`, `mrr`, `faithfulness`,
`answer_relevancy`). Isso satisfaz a dependência no nível dos DADOS
(a baseline representa as métricas que RAG-061/062 medem) sem
acoplamento de CÓDIGO entre as três branches — RAG-063 foi ramificada
diretamente de `master`, tão independente quanto RAG-061.

**Schema `Baseline`** (`config/evaluation/<id>.<version>.yaml`, mesma
convenção de versão imutável de `packages/generation/prompts.py` e
`packages/evaluation/golden_dataset.py`): `id`, `version`, `measured`
(bool), `max_regression_pct` (fração, `0.05` = 5%), `metrics`
(`dict[str, float]`), `limitations` (tupla de strings, não-vazia —
critério de aceite "limitações documentadas" verificado como
invariante do schema, não só como uma seção escrita à mão uma vez).
`minimum_acceptable(metric)` devolve `baseline * (1 -
max_regression_pct)`; `check_regression(current_metrics, *,
baseline)` compara um `dict` de métricas correntes contra a baseline e
devolve um `RegressionCheck(passed, violations)` — ignora silenciosamente
qualquer métrica presente só de um dos lados (permite comparar contra
um relatório parcial, por exemplo só de retrieval ou só de geração,
sem falhar por métricas que aquele relatório nunca teve).

**"limitações documentadas"** (critério de aceite) tem duas camadas:
o schema `Baseline.limitations` (estrutural, sempre não-vazio) e o
conteúdo real de `config/evaluation/poc.v1.yaml`, que documenta a
limitação mais importante desta atividade — `measured: false`: os
valores gravados são as METAS da seção 21 do plano, não uma medição
real (escrever esta atividade não teve acesso a um gateway LiteLLM
alcançável, mesma limitação já documentada nos dois scripts de
avaliação, seção 15 do plano). A primeira execução real de
`scripts/run_retrieval_evaluation.py`/`run_generation_evaluation.py`
contra um ambiente com gateway ativo deve avaliar se as metas foram
atingidas e, em caso positivo, publicar `poc.v2.yaml` com os
valores medidos e `measured: true` — `poc.v1.yaml` nunca deve ser
editado depois de publicado. `answer_relevancy` não tem meta própria
na seção 21 do plano — reusa a meta de faithfulness (0,85), mesma
decisão já tomada em `run_generation_evaluation.py`.

**`scripts/check_evaluation_baseline.py`** — CLI que lê um ou mais
relatórios JSON já gravados em disco pelos dois scripts de avaliação
(`--report`, repetível) como `dict` genérico via `json.load` (nunca um
import Python das branches de RAG-061/062), combina as chaves
numéricas de nível superior de todos eles e chama `check_regression`
contra `get_current_baseline()`. Ao contrário de
`run_retrieval_evaluation.py`/`run_generation_evaluation.py`, este
script não chama nenhum modelo real — só lê JSON e compara números —
por isso É testado diretamente em `tests/unit/
test_check_evaluation_baseline.py` (mesmo padrão de
`scripts/check_security_exceptions.py`, RAG-071).

Testes: `test_baseline.py` (schema, carregamento, cache, mismatch
id/versão vs. nome do arquivo, `minimum_acceptable`, e uma bateria de
`check_regression` cobrindo aprovação exata, melhora, regressão dentro
do limite, regressão além do limite, fronteira exata do limite,
múltiplas violações, métrica ausente de um lado ou de outro, valor de
baseline zero sem divisão por zero, e o texto da mensagem de
violação — 100% de cobertura); `test_check_evaluation_baseline.py`
(extração de métricas de um ou mais relatórios, filtragem de
booleanos, código de saída 0/1 — 100% de cobertura).

## Deploy automatizado em DEV (RAG-074)

Objetivo (seção 21/16.3 do plano): "implantar digest publicado e
executar smoke test"; critério de aceite: "ambiente GitHub
`development`; deploy rastreável; falha não promove release".

**O que "ambiente DEV" significa nesta atividade** — decisão de escopo
mais importante desta atividade, documentada tanto no workflow quanto
no compose file: este repositório (uma POC) não tem nenhum servidor
DEV persistente provisionado em lugar nenhum — nenhuma credencial de
cloud, Terraform ou host remoto existe em qualquer lugar do projeto.
"Implantar em DEV" foi interpretado como: subir a imagem PUBLICADA
(nunca reconstruída, seção 16.5: "não reconstruir imagem durante
promoção") numa stack efêmera na própria runner do GitHub Actions,
aplicar as migrations e rodar um smoke test contra ela — a mesma
imagem que rodaria num host real, só que o host é descartável. Isso
satisfaz o critério de aceite literalmente sem fabricar uma
infraestrutura persistente que não existe. Quando um host DEV real for
provisionado, só o passo "subir a stack" muda (SSH/kubectl para o host
real em vez de `docker compose` na runner) — a composição de serviços
e o smoke test continuam os mesmos.

**`deploy/compose/docker-compose.dev.yml`** (novo — o diretório
`deploy/compose/` já existia, só com um `.gitkeep`, seção 7 do plano)
— mesmos serviços de infraestrutura de `docker-compose.yml` (RAG-003:
Postgres/pgvector, Redis, MinIO, Ollama + pull do modelo de embeddings,
LiteLLM), sem a stack de observabilidade (não necessária para um smoke
test) mais `api`/`worker`, cuja `image:` é obrigatória via
`${API_IMAGE:?...}`/`${WORKER_IMAGE:?...}` (sem default: este arquivo
só faz sentido apontando para uma tag já publicada, nunca para uma
imagem local). Credenciais são valores efêmeros só desta stack
descartável (nunca reaproveitados). `OTEL_TRACES_ENABLED`/
`OTEL_METRICS_ENABLED=false` mantêm a instrumentação ativa contra o
tracer/meter no-op, sem exigir o collector de pé.

**`.github/workflows/deploy-dev.yml`** (novo) — dispara via
`workflow_run` quando o workflow "Publish" (RAG-072) termina com
sucesso em `master`, e também via `workflow_dispatch` (execução
manual). Resolve o SHA publicado, faz checkout exatamente nele (não
necessariamente a HEAD atual), sobe a infraestrutura
(`docker compose ... up -d --wait`), aplica `alembic upgrade head` A
PARTIR DO CÓDIGO-FONTE checked out (não de dentro do container: a
imagem publicada não inclui `migrations/`/`alembic.ini`, só o código
de aplicação, RAG-072) contra o Postgres já saudável, sobe `api`/
`worker` com as imagens publicadas (`sha-<sha>`, nunca `:latest`),
roda um smoke test (`GET /health/live` e `/health/ready` esperando
200), publica um resumo no workflow e sempre derruba a stack ao final
(`always()`).

**"ambiente GitHub development"**: satisfeito pelo `environment:
development` do job — cria automaticamente um registro de Deployment
do GitHub (aba "Environments"/"Deployments" do repositório, rastreável
ao SHA exato e ao resultado da run), sem nenhuma chamada manual à API
de Deployments.

**"falha não promove release"**: nenhum passo real usa
`continue-on-error` — qualquer falha (infra não sobe, migration falha,
smoke test recebe status != 200) falha o job inteiro, e portanto o
Deployment do ambiente `development`. RAG-075 (ainda não implementada)
deve promover só um digest cujo deploy em DEV tenha `conclusion:
success` — verificável pela API de Deployments do ambiente ou pelo
resultado desta run.

**Aviso de verificação — importante para revisão**: este workflow foi
validado com `actionlint` (sintaxe/expressões, sem achados, incluindo
os workflows já existentes) e revisado manualmente linha a linha
contra `docker-compose.yml`/os Dockerfiles, mas NUNCA rodou de
verdade — o ambiente onde esta atividade foi implementada não tem
Docker disponível para testar a stack de ponta a ponta. Ao contrário
de RAG-061/062/063/073 (validados com pytest real), a corretude
funcional deste workflow especificamente NÃO foi comprovada por execução.
Recomenda-se fortemente disparar manualmente uma vez
(`workflow_dispatch`) e observar o resultado antes de depender dele no
fluxo de release.
## Quality gate de RAG no CI (RAG-073)

Objetivo (seção 21 do plano): "executar avaliação reduzida em
mudanças relevantes"; critério de aceite: "paths filters cobrem
código, prompts, retrieval e modelos; relatório fica disponível no
workflow".

**`.github/workflows/rag-quality-gate.yml`** — novo workflow (convive
com `pull-request.yml`/RAG-070 e `security.yml`/RAG-071, nenhum dos
dois roda modelo real): dispara em toda PR contra `master` que toca
`packages/retrieval/**`, `packages/generation/**`,
`packages/evaluation/**`, `packages/application/queries/retrieval.py`,
`packages/application/commands/query.py`, `adapters/vector_search/**`,
`adapters/lexical_search/**`, `adapters/reranker/**`,
`adapters/litellm/**`, `config/prompts/**`, `config/models/**`,
`config/evaluation/**`, `datasets/golden/**` ou os três scripts de
avaliação — e também sob demanda via `workflow_dispatch` (com um
input `max_cases` para uma checagem manual mais ampla). Roda, nessa
ordem: `scripts/run_retrieval_evaluation.py --max-cases N`,
`scripts/run_generation_evaluation.py --max-cases N`,
`scripts/check_evaluation_baseline.py` (comparando os dois relatórios
contra `poc.v1.yaml`, RAG-063); publica os relatórios Markdown no
resumo do workflow (`$GITHUB_STEP_SUMMARY`, critério de aceite
"relatório fica disponível no workflow") e os JSON/Markdown como
artefato (`actions/upload-artifact`); falha a PR se qualquer uma das
três verificações falhar.

**"Reduzida"**: em vez do dataset dourado inteiro (~35 casos
respondíveis), avalia só os `N` primeiros (default 3) — `max_cases`,
parâmetro novo desta atividade em `evaluate_retrieval`/
`evaluate_generation` (`packages/evaluation/retrieval_evaluation.py`/
`generation_evaluation.py`) e `--max-cases` nos dois scripts
correspondentes. Manter o dataset dourado no tamanho mínimo do schema
(30 casos, RAG-060) e reduzir só QUANTOS são de fato processados evita
precisar de uma segunda versão "pequena" do dataset só para CI.

**Por que este workflow não é bloqueado por "chamadas reais ficam em
workflow manual ou agendado com orçamento limitado" (seção 15 do
plano)**: ele PRÓPRIO é esse workflow com orçamento limitado — dedicado,
fora de `pull-request.yml`/RAG-070 (que continua simulando todo
provedor de LLM, inalterado) — só que reage a PRs relevantes via
`paths` (o critério de aceite desta atividade exige exatamente isso) em
vez de só manual/agendado, com o orçamento limitado por `--max-cases`
em vez de por frequência de execução.

**Por que o job pula sem bloquear quando não há gateway configurado**:
este repositório ainda não cadastra nenhum secret de gateway LiteLLM
real (nenhum workflow existente usa `secrets.*` além do `GITHUB_TOKEN`
embutido, confirmado por `grep`) — rodar os scripts sem um gateway
alcançável falharia com erro de conexão, não com uma métrica abaixo do
limiar, o que bloquearia toda PR relevante por um motivo de
infraestrutura ausente, não de qualidade do RAG. O job checa a
presença de `secrets.LITELLM_BASE_URL` antes de qualquer chamada real
e, se ausente, publica um aviso no resumo do workflow e termina com
sucesso — cadastrar `LITELLM_BASE_URL` (e `LITELLM_API_KEY`, se o
gateway exigir) como secrets do repositório ativa as chamadas de
verdade sem nenhuma mudança neste arquivo. Este é o mesmo tipo de
limitação já documentado em `config/evaluation/poc.v1.yaml`
(`measured: false`).

**`make rag-quality-gate`** — reproduz localmente os três passos
reais do workflow (exige `LITELLM_BASE_URL` configurado no ambiente,
ao contrário de `make check`); nunca faz parte de `make check`/CI
comum, mesmo racional do workflow.

Testes: `evaluate_retrieval`/`evaluate_generation` ganharam testes
para `max_cases` (`test_retrieval_evaluation.py`,
`test_generation_evaluation.py` — limita corretamente quantos casos
são processados; um `max_cases` maior que o dataset avalia tudo, sem
erro). O workflow em si (`rag-quality-gate.yml`) foi validado com
`actionlint` (sem achados, incluindo os workflows já existentes) —
não tem um teste unitário Python, mesmo padrão de qualquer outro
arquivo `.github/workflows/*.yml` do projeto (nenhum deles é testado
por `pytest`).

## Release e produção (RAG-075)

Objetivo (seção 16.4/21 do plano): "promover o mesmo digest com
aprovação manual"; critério de aceite: "usa environment protegido;
avaliação completa; rollback documentado e testado".

**`.github/workflows/release.yml`** (novo) — dois jobs, na ordem da
seção 16.4 do plano:

1. `validate`: resolve o commit/versão do release (push de tag
   `v*.*.*`, ou `workflow_dispatch` com `sha`/`version` explícitos —
   este segundo caminho é também o mecanismo de rollback, ver abaixo);
   confirma via a API de Deployments do GitHub que esse commit tem
   pelo menos um deploy com `state: success` no ambiente `development`
   (criado por `deploy-dev.yml`, RAG-074) — "selecionar o digest já
   validado em DEV" — recusando promover qualquer commit que nunca
   passou por DEV; roda a avaliação COMPLETA (RAG-061/062/063, sem
   `--max-cases`, ao contrário do quality gate reduzido de RAG-073) —
   "executar avaliação completa".
2. `promote`: atrás de `environment: production` — "usa environment
   protegido"/"solicitar aprovação pelo GitHub Environment" (seção
   16.4, passo 4); re-tagueia as imagens já publicadas
   (`sha-<sha>` → a tag semântica do release) via `docker buildx
   imagetools create` — cópia de manifesto no registry, nunca um
   rebuild ("não reconstruir imagem durante promoção"/"não usar
   `latest` em deployments", seção 16.5); aplica as migrations
   (`alembic upgrade head`, a partir do código-fonte no commit exato,
   mesmo racional de RAG-074) e sobe a MESMA stack efêmera de
   smoke-deploy de RAG-074 (`deploy/compose/docker-compose.dev.yml`,
   reutilizada sem nenhuma mudança — só as tags de imagem diferem)
   como validação final.

**"aprovação manual"/"environment protegido"**: `environment:
production` por si só não bloqueia nada até alguém configurar
"Required reviewers" para o ambiente `production` em Settings →
Environments no GitHub — isso é decisão/configuração de repositório,
fora do escopo de um arquivo de workflow, e está documentado no
cabeçalho do arquivo para quem for configurar.

**Limitações documentadas desta atividade** (mesma honestidade de
RAG-074 — este repositório, uma POC, não tem nenhum ambiente de
produção persistente provisionado):

- "Fazer deploy progressivo" (seção 16.4, passo 7) NÃO é implementado
  de verdade: exigiria múltiplas réplicas e um balanceador deslocando
  tráfego aos poucos, infraestrutura que este POC não tem. O
  smoke-deploy completo do job `promote` é a aproximação disponível,
  não um rollout progressivo real.
- "Verificar métricas e executar rollback se necessário" (passo 8):
  não há um dashboard de produção de verdade para checar
  automaticamente — fica para quando um ambiente real existir.

**Rollback — documentado e testado (critério de aceite)**: o
mecanismo é disparar `release.yml` via `workflow_dispatch` apontando
`sha`/`version` para um release anterior já validado em DEV (por
exemplo, `sha` do commit da versão anterior e `version:
v1.2.2-rollback-1`) — isso repromove exatamente aquele digest antigo
pelo MESMO caminho de código do promote normal (checagem de deploy em
DEV bem-sucedido, avaliação completa, aprovação manual, re-tag sem
rebuild, migration, smoke test), nunca um branch de rollback separado
e não testado à parte. "Testado" aqui significa: é o mesmo código já
exercitado por um release normal, não uma segunda implementação
paralela que só seria descoberta quebrada no primeiro rollback real.

**Aviso de verificação — importante para revisão**: como RAG-074, este
workflow foi validado com `actionlint` (sem achados, incluindo os
workflows já existentes) e revisão manual linha a linha, mas NUNCA
rodou de verdade (sem Docker disponível no ambiente onde foi
implementado, e sem um ambiente `production` configurado neste
repositório para observar um approval gate real). Recomenda-se
fortemente testar via `workflow_dispatch` contra um commit já validado
em DEV antes de depender dele para uma promoção real, e configurar os
revisores obrigatórios do ambiente `production` antes do primeiro uso.

Sem mudança de código Python nesta atividade (nenhum arquivo `.py`
tocado) — ruff/mypy/pytest/bandit/pip-audit seguem no baseline de
`master`, sem regressão. Sem migração.
## Teste E2E principal (RAG-080)

`tests/e2e/test_rag_pipeline.py` cobre, de ponta a ponta e contra a
stack real (Postgres/pgvector, MinIO, gateway LiteLLM — `docker compose
up -d`, RAG-003), o fluxo completo do plano (seção 22, "teste E2E
principal"): cria uma base de conhecimento, envia um documento, indexa,
consulta com geração e valida que a resposta cita o documento certo —
mais um segundo cenário dedicado a isolamento entre tenants.

**Sem nenhum `app.dependency_overrides`** — ao contrário de toda a
suíte em `tests/unit` (que troca Postgres/MinIO/LiteLLM reais por fakes
em memória): `tests/e2e` sobe `apps.api.main.app` exatamente como ela
roda em produção, contra as portas concretas de verdade. É essa
diferença que faz este teste ser "principal"/E2E, e não mais um teste
de integração — mas também é o motivo de exigir infraestrutura real de
pé para rodar (ver "Como rodar" abaixo).

**Autenticação**: reaproveita `scripts/mint_local_dev_token.py::mint_token`
(RAG-050) para mintar um JWT real, assinado com o `JWT_SECRET` de
verdade lido de `get_settings()` — nunca um segredo de teste próprio,
já que não há `dependency_overrides` do verificador de token para
substituir.

**Indexação sem worker Celery real**: `tests/e2e/test_rag_pipeline.py`
chama `apps.indexing_worker.tasks._run_attempt(index_job_id,
attempt_number=1, max_attempts=5)` diretamente, em vez de publicar o
job e esperar um worker consumir a fila. `_run_attempt` é a mesma
função de negócio que a task Celery (`process_index_job_task`) chama
por baixo — só invocada aqui de forma síncrona e in-process, sem exigir
um worker separado rodando durante o teste. Isso simplifica o teste sem
abrir mão de exercitar o pipeline de indexação real (Docling, chunking,
embeddings via LiteLLM, persistência — RAG-023 a RAG-026).

**Fixture conhecida**: `tests/e2e/fixtures/nimbus-rewards.md` descreve
um programa de fidelidade inteiramente fictício ("Nimbus Rewards"), com
um código secreto ("GIRASSOL-7") que não existe em nenhum outro lugar
— nem no treinamento do modelo de geração, nem em qualquer outro
documento deste projeto. A pergunta do teste ("qual o código secreto
de ativação...") só tem resposta correta se o modelo realmente recebeu
o chunk certo como contexto — validação mais forte de "grounded" do que
comparar a resposta gerada contra um texto exato (a saída de um LLM
real não é determinística entre execuções): o teste verifica
`grounded=True` e que a citação aponta para o documento certo
(`document_name`), nunca o texto literal da resposta.

**Isolamento entre tenants**: um segundo cenário
(`test_isolamento_entre_tenants`) cria uma base de conhecimento como
tenant A e confirma que tenant B recebe 404 (nunca 403 — seção 13 do
plano) ao tentar ler a base, fazer upload de documento nela ou
consultá-la — mesmo padrão "404, nunca 403" já usado em toda a API
(RAG-012, RAG-021, RAG-044).

**Por que `tests/e2e` nunca roda em `make test`/CI comum**: exige
Postgres/MinIO/LiteLLM reais de pé, ao contrário de toda a suíte em
`tests/unit`, que roda em qualquer lugar com só as dependências Python
instaladas. Rodá-lo em `pull-request.yml` (RAG-070) falharia sempre —
não por regressão, mas por falta de infraestrutura — bloqueando toda PR
por um motivo desconectado da qualidade do código, o mesmo racional já
aplicado ao quality gate de RAG (RAG-073) e ao deploy DEV (RAG-074).
Duas mudanças garantem esse isolamento:

- `pyproject.toml`: `[tool.pytest.ini_options].testpaths` mudou de
  `["tests"]` para `["tests/unit"]` — um `pytest`/`make test` sem
  argumentos agora só coleta `tests/unit` (os 740 testes existentes,
  inalterados), nunca `tests/e2e` (nem os outros diretórios ainda
  vazios, `tests/integration`/`tests/contract`/`tests/evaluation`).
  Antes desta mudança, criar `tests/e2e/*.py` faria um `pytest` comum
  tentar coletar e rodar esses arquivos também — e falhar, sem
  Postgres/MinIO/LiteLLM disponíveis — quebrando o CI verde existente.
- `Makefile`: novo alvo `make e2e` (`pytest tests/e2e --no-cov`),
  documentado como exigindo `docker compose up -d` (RAG-003) e um
  `.env` real — mesmo espírito de `make rag-quality-gate` (RAG-073),
  um alvo "requer infraestrutura real" mantido fora de `make
  check`/CI comum. `--no-cov` evita que o teto de cobertura de 85%
  (`[tool.coverage.report].fail_under`, medido sobre `tests/unit`) seja
  avaliado — sem sentido — contra uma execução de só dois testes E2E.

**Como rodar**: `docker compose up -d` (RAG-003) para subir a
infraestrutura local, garantir um `.env` real configurado
(`JWT_SECRET`/`JWT_ISSUER`/`JWT_AUDIENCE`, credenciais de Postgres/MinIO,
`LITELLM_BASE_URL` apontando para o LiteLLM local), aplicar as
migrations (`alembic upgrade head`) e então `make e2e`.

**Estado de verificação — aviso importante**: este teste foi escrito e
validado com `ruff format`/`ruff check`/`mypy` (100% limpo, incluindo
`tests/e2e/`) e com a suíte `tests/unit` completa (740 testes, cobertura
93,43%, confirmando que a mudança de `testpaths` não altera o
comportamento do CI existente) — mas **nunca foi executado de fato**:
o ambiente onde esta atividade foi implementada não tem Docker
disponível para subir Postgres/pgvector/MinIO/LiteLLM. Recomenda-se
fortemente rodar `make e2e` localmente (com a stack de pé) antes de
considerar este critério de aceite satisfeito — a mesma ressalva já
feita para os workflows de deploy DEV (RAG-074) e release (RAG-075).

## Correção pós-validação de RAG-080: `pytest-asyncio` e isolamento de `.env`

Ao validar `make e2e` de verdade (RAG-080) contra um checkout com
Docker disponível, dois problemas surgiram — nenhum causado pelo código
do teste E2E em si, ambos expostos só agora porque é a primeira vez que
alguém roda a suíte com uma fixture assíncrona geradora de verdade
(`tests/e2e/conftest.py::api_client`) e com um `.env` real presente no
repositório:

**1. `pytest-asyncio` incompatível com `pytest` 9** — `pyproject.toml`
fixava `pytest-asyncio>=0.23,<1.0`, mas as versões 0.23 a 0.26 do
`pytest-asyncio` exigem `pytest<8.2`/`<9`, incompatível com
`pytest>=9.0.3` já fixado no projeto. O resolvedor de dependências
"resolvia" isso escolhendo a versão mais antiga da faixa permitida
(0.23.3), que tem um bug conhecido com fixtures assíncronas geradoras
sob `pytest` 9 (`'FixtureDef' object has no attribute 'unittest'`,
`pytest_asyncio/plugin.py::_asyncgen_fixture_wrapper`). Como nenhum
teste em `tests/unit` usa esse padrão de fixture, o bug nunca tinha
aparecido antes de `tests/e2e/conftest.py` (RAG-080). Corrigido
alargando a faixa para `pytest-asyncio>=1.0,<2.0` — a partir da 1.0 o
pacote é compatível com `pytest` 9.

**2. Teste de `mint_local_dev_token.py` dependia da ausência de um
`.env` real** — `tests/unit/test_mint_local_dev_token.py::test_main_returns_one_and_prints_error_on_configuration_error`
simula `JWT_SECRET` ausente removendo só a variável de ambiente
(`monkeypatch.delenv`), mas `Settings` (RAG-004) também lê um arquivo
`.env` (caminho relativo, `env_file=".env"`) — se um `.env` de verdade
existir no diretório de trabalho (exatamente o que RAG-080 pede para
criar antes de `make e2e`), `get_settings()` recupera `JWT_SECRET` de
lá mesmo com a variável removida, e o teste falha (esperava
`exit_code == 1`, recebia `0`). Nenhum outro teste do arquivo tem esse
problema: todos os outros definem as variáveis via `monkeypatch.setenv`,
que tem precedência sobre o arquivo `.env` (ordem de precedência do
Pydantic Settings) — só o teste do "caminho ausente" depende da
ausência total da variável em qualquer fonte. Corrigido com
`monkeypatch.chdir(tmp_path)` nesse teste, isolando-o de qualquer
`.env` real do repositório — mesmo racional de `load_settings(env_file=None)`
já documentado em `packages/config/settings.py` para os testes de
`test_settings.py`.

**Validação**: reproduzido e confirmado o bug do `pytest-asyncio` num
teste mínimo isolado antes de alterar a versão; suíte `tests/unit`
completa (740 testes, cobertura 93,43%) rodando com um `.env` real
presente no diretório de trabalho (mesma condição do ambiente local de
Marcos); `tests/e2e` agora progride de fato até tentar conectar no
Postgres (`ConnectionRefusedError` em `127.0.0.1:5432` — confirma que a
fixture assíncrona não quebra mais antes disso; a stack real continua
sendo o único jeito de rodar o cenário até o fim, sem Docker
disponível neste ambiente). `ruff format`/`ruff check`/`mypy` limpos em
todo o projeto; `bandit`/`pip-audit` sem achados.
