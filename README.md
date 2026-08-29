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

## Desenvolvimento local

Pré-requisitos: Python 3.12.

```bash
make install    # cria .venv e instala dependências de desenvolvimento
make lint       # ruff check + ruff format --check
make typecheck  # mypy sobre apps/, packages/, adapters/ e tests/
make test       # pytest com relatório de cobertura (term-missing + XML)
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
