"""Testes de RAG-013: tratamento padronizado de erros (Problem Details).

Não há ainda endpoints de negócio reais (chegam em RAG-012) para
exercitar cada status naturalmente, então este teste monta uma FastAPI
de mentira, só para o teste, com uma rota por categoria de erro — os
handlers testados são exatamente os registrados em `apps/api/main.py`
via `register_error_handlers` (a mesma função, não uma cópia).
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from apps.api.errors import PROBLEM_JSON_MEDIA_TYPE, REQUEST_ID_HEADER, register_error_handlers
from packages.application.errors import (
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from packages.domain.enums.document_status import DocumentStatus
from packages.domain.exceptions.errors import InvalidStatusTransitionError


class _Item(BaseModel):
    name: str
    quantity: int


def _build_test_app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom/invalid-request")
    def _invalid_request() -> None:
        raise InvalidRequestError("cabeçalho Idempotency-Key malformado")

    @app.get("/boom/auth")
    def _auth() -> None:
        raise AuthenticationError()

    @app.get("/boom/forbidden")
    def _forbidden() -> None:
        raise PermissionDeniedError("tenant não autorizado")

    @app.get("/boom/not-found")
    def _not_found() -> None:
        raise NotFoundError("knowledge base não encontrada")

    @app.get("/boom/conflict")
    def _conflict() -> None:
        raise ConflictError("já existe uma base com esse nome")

    @app.get("/boom/unprocessable")
    def _unprocessable() -> None:
        raise UnprocessableEntityError("regra de negócio violada")

    @app.get("/boom/domain-error")
    def _domain_error() -> None:
        document_status = DocumentStatus.DELETED
        raise InvalidStatusTransitionError(
            entity="Document", current=document_status.value, attempted="PROCESSING"
        )

    @app.get("/boom/http-exception")
    def _http_exception() -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=418, detail="sou um bule de chá")

    @app.get("/boom/unhandled")
    def _unhandled() -> None:
        raise RuntimeError("segredo-interno-que-nunca-pode-vazar-para-o-cliente")

    @app.post("/echo-item")
    def _echo_item(item: _Item) -> _Item:
        return item

    @app.get("/ok")
    def _ok() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_build_test_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_title"),
    [
        ("/boom/invalid-request", 400, "Requisição inválida"),
        ("/boom/auth", 401, "Autenticação necessária"),
        ("/boom/forbidden", 403, "Acesso negado"),
        ("/boom/not-found", 404, "Recurso não encontrado"),
        ("/boom/conflict", 409, "Conflito"),
        ("/boom/unprocessable", 422, "Entidade não processável"),
    ],
)
def test_application_errors_map_to_the_right_status_and_problem_shape(
    client: TestClient, path: str, expected_status: int, expected_title: str
) -> None:
    response = client.get(path)

    assert response.status_code == expected_status
    assert response.headers["content-type"] == PROBLEM_JSON_MEDIA_TYPE
    body = response.json()
    assert body["status"] == expected_status
    assert body["title"] == expected_title
    assert body["instance"] == path
    assert body["request_id"]
    assert response.headers[REQUEST_ID_HEADER] == body["request_id"]


def test_domain_error_maps_to_409_conflict(client: TestClient) -> None:
    response = client.get("/boom/domain-error")

    assert response.status_code == 409
    body = response.json()
    assert body["title"] == "Conflito de estado"
    assert "DELETED" in body["detail"]
    assert "PROCESSING" in body["detail"]


def test_http_exception_is_rendered_as_problem_details(client: TestClient) -> None:
    response = client.get("/boom/http-exception")

    assert response.status_code == 418
    body = response.json()
    assert body["status"] == 418
    assert body["detail"] == "sou um bule de chá"


def test_pydantic_validation_error_returns_422_with_field_errors(client: TestClient) -> None:
    response = client.post("/echo-item", json={"name": "café", "quantity": "não-é-um-número"})

    assert response.status_code == 422
    body = response.json()
    assert body["title"] == "Erro de validação"
    assert body["errors"]
    assert any(err["loc"][-1] == "quantity" for err in body["errors"])
    # `ctx`/`input` nunca aparecem — só type/loc/msg (não ecoam o valor enviado).
    for error in body["errors"]:
        assert set(error) == {"type", "loc", "msg"}


def test_unhandled_exception_returns_500_without_leaking_internals(client: TestClient) -> None:
    response = client.get("/boom/unhandled")

    assert response.status_code == 500
    assert "segredo-interno-que-nunca-pode-vazar-para-o-cliente" not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text
    body = response.json()
    assert body["title"] == "Erro interno"
    assert body["request_id"]


def test_request_id_is_echoed_back_when_the_client_supplies_one(client: TestClient) -> None:
    response = client.get("/ok", headers={REQUEST_ID_HEADER: "meu-id-de-correlacao"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "meu-id-de-correlacao"


def test_request_id_is_generated_when_the_client_does_not_supply_one(client: TestClient) -> None:
    response = client.get("/ok")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


def test_request_id_differs_across_independent_requests(client: TestClient) -> None:
    first = client.get("/ok").headers[REQUEST_ID_HEADER]
    second = client.get("/ok").headers[REQUEST_ID_HEADER]

    assert first != second
