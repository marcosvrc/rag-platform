"""Teste E2E principal (RAG-080, critério de aceite do épico E8).

Cenário ponta a ponta contra a stack real (Postgres/pgvector, MinIO,
gateway LiteLLM — RAG-003, `docker compose up -d`), sem NENHUM
`app.dependency_overrides` (ao contrário de toda a suíte em
`tests/unit`): cria uma base de conhecimento, envia um documento
conhecido, processa a indexação de forma síncrona (chamando
`_run_attempt` diretamente — ver docstring de `_index_document_sync`
abaixo, sem depender de um worker Celery real de pé), consulta com uma
pergunta cuja resposta correta só existe nesse documento, e valida
isolamento entre dois tenants.

## Por que "GIRASSOL-7"

`tests/e2e/fixtures/nimbus-rewards.md` descreve um programa de
fidelidade inteiramente fictício ("Nimbus Rewards"), com um código
secreto que não existe em nenhum outro lugar do universo — nem no
treinamento do modelo de geração, nem em qualquer outro documento deste
repositório. Se a resposta gerada cita esse código, é prova objetiva de
que ela veio da recuperação (RAG), não do conhecimento prévio do
modelo — a validação mais forte possível de "grounded" sem depender de
comparação exata de texto (a saída de um LLM real não é determinística
entre execuções).

## Por que não roda em `make test`/CI comum

Ver `tests/e2e/conftest.py` e o comentário de `testpaths` em
`pyproject.toml`: este teste exige infraestrutura real de pé, então só
roda via `make e2e`, nunca em `pytest`/`make test`/o workflow de PR
padrão (RAG-070).

## Estado de verificação

Escrito e revisado (ruff, mypy) sem acesso a Docker no ambiente onde
foi implementado — nunca executado de fato. Antes de confiar neste
teste, rode `docker compose up -d` (RAG-003) e depois `make e2e`
localmente.
"""

from __future__ import annotations

from uuid import UUID

import httpx

from apps.indexing_worker.tasks import _run_attempt
from packages.application.commands.index_job import IndexJobAttemptOutcome
from packages.domain.enums.processing_status import ProcessingStatus

# `asyncio_mode = "auto"` (pyproject.toml) já cobre funções `async def
# test_...` sem precisar de `@pytest.mark.asyncio` — mesmo padrão do
# resto da suíte (ver tests/unit/test_knowledge_base_use_cases.py).

_QUESTION_ABOUT_THE_FIXTURE = (
    "Qual é o código secreto de ativação necessário para resgatar pontos na Nimbus Rewards?"
)


async def _index_document_sync(index_job_id: UUID) -> None:
    """Processa o job de indexação síncrono e in-process, sem depender
    de um worker Celery real consumindo a fila (que este ambiente de
    teste E2E não sobe) — `_run_attempt` é a mesma função de negócio
    que `apps.indexing_worker.tasks.process_index_job_task` chama via
    Celery, só invocada diretamente aqui (RAG-022/RAG-026)."""
    outcome = await _run_attempt(index_job_id, attempt_number=1, max_attempts=5)
    assert outcome is IndexJobAttemptOutcome.SUCCEEDED, (
        f"indexação deveria suceder na 1ª tentativa contra a fixture conhecida, "
        f"mas terminou com {outcome!r} — confira os logs da stack (`docker compose logs`)"
    )


async def _create_knowledge_base(
    api_client: httpx.AsyncClient, *, headers: dict[str, str], name: str
) -> str:
    response = await api_client.post("/v1/knowledge-bases", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def test_fluxo_completo_upload_indexacao_e_consulta_com_citacao(
    api_client: httpx.AsyncClient,
    tenant_a_headers: dict[str, str],
    known_fixture_document: tuple[str, bytes, str],
) -> None:
    """Critérios de aceite cobertos: "cria KB, indexa documento
    conhecido, consulta e valida resposta com citação corretas"."""
    knowledge_base_id = await _create_knowledge_base(
        api_client, headers=tenant_a_headers, name="E2E — Nimbus Rewards"
    )

    filename, content, mime_type = known_fixture_document
    upload_response = await api_client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, content, mime_type)},
        headers=tenant_a_headers,
    )
    assert upload_response.status_code == 202, upload_response.text
    upload_body = upload_response.json()
    index_job_id = UUID(upload_body["index_job_id"])

    await _index_document_sync(index_job_id)

    job_response = await api_client.get(f"/v1/jobs/{index_job_id}", headers=tenant_a_headers)
    assert job_response.status_code == 200, job_response.text
    assert job_response.json()["status"] == ProcessingStatus.SUCCEEDED

    query_response = await api_client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": _QUESTION_ABOUT_THE_FIXTURE},
        headers=tenant_a_headers,
    )
    assert query_response.status_code == 200, query_response.text
    query_body = query_response.json()

    # "Grounded" é a validação central deste teste — nunca comparamos o
    # texto da resposta contra um valor exato (saída de LLM real não é
    # determinística): a prova de que a resposta veio do documento
    # certo é a citação apontar para a fixture conhecida.
    assert query_body["grounded"] is True, query_body
    citations = query_body["citations"]
    assert len(citations) >= 1, query_body
    assert any(citation["document_name"] == filename for citation in citations), citations


async def test_isolamento_entre_tenants(
    api_client: httpx.AsyncClient,
    tenant_a_headers: dict[str, str],
    tenant_b_headers: dict[str, str],
    known_fixture_document: tuple[str, bytes, str],
) -> None:
    """Critério de aceite "valida isolamento entre dois tenants": um
    recurso do tenant A é sempre 404 para o tenant B — nunca 403 (seção
    13 do plano, "404, nunca 403" — ver também `test_query_router.py`,
    RAG-044)."""
    knowledge_base_id = await _create_knowledge_base(
        api_client, headers=tenant_a_headers, name="E2E — isolamento (tenant A)"
    )

    get_as_other_tenant = await api_client.get(
        f"/v1/knowledge-bases/{knowledge_base_id}", headers=tenant_b_headers
    )
    assert get_as_other_tenant.status_code == 404, get_as_other_tenant.text

    filename, content, mime_type = known_fixture_document
    upload_as_other_tenant = await api_client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": (filename, content, mime_type)},
        headers=tenant_b_headers,
    )
    assert upload_as_other_tenant.status_code == 404, upload_as_other_tenant.text

    query_as_other_tenant = await api_client.post(
        f"/v1/knowledge-bases/{knowledge_base_id}/query",
        json={"query": _QUESTION_ABOUT_THE_FIXTURE},
        headers=tenant_b_headers,
    )
    assert query_as_other_tenant.status_code == 404, query_as_other_tenant.text
