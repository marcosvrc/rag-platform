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
