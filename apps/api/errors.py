"""Tratamento padronizado de erros da API (RAG-013).

Toda resposta de erro — 400/401/403/404/409/422 e qualquer exceção não
tratada (500) — vira Problem Details (`packages/contracts/problem_details.py`),
nunca stack trace, nunca detalhe interno. Cada requisição ganha um
`request_id` de correlação (lido de `X-Request-ID` se o cliente enviar
um, senão gerado); ele volta tanto no corpo quanto no header da resposta,
inclusive quando a resposta é de sucesso.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from packages.application.errors import (
    ApplicationError,
    AuthenticationError,
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    PermissionDeniedError,
    UnprocessableEntityError,
)
from packages.contracts.problem_details import ProblemDetail
from packages.domain.exceptions.errors import DomainError

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
PROBLEM_JSON_MEDIA_TYPE = "application/problem+json"

_STATUS_BY_APPLICATION_ERROR: dict[type[ApplicationError], int] = {
    InvalidRequestError: status.HTTP_400_BAD_REQUEST,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    UnprocessableEntityError: status.HTTP_422_UNPROCESSABLE_CONTENT,
}


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Atribui (ou propaga) o `request_id` de correlação desta requisição
    antes de qualquer handler rodar, para que até um 500 tenha um."""
    request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return str(request_id) if request_id else str(uuid.uuid4())


def _problem_response(
    request: Request,
    *,
    status_code: int,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    request_id = _request_id(request)
    problem = ProblemDetail(
        title=title,
        status=status_code,
        detail=detail,
        instance=request.url.path,
        request_id=request_id,
        errors=errors,
    )
    return JSONResponse(
        status_code=status_code,
        content=problem.model_dump(exclude_none=True),
        media_type=PROBLEM_JSON_MEDIA_TYPE,
        headers={REQUEST_ID_HEADER: request_id},
    )


def _serialize_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    """Só os campos seguros de cada erro do Pydantic (`type`/`loc`/`msg`)
    — `ctx`/`input` ficam de fora: podem não ser serializáveis em JSON e
    podem ecoar de volta o valor inválido enviado pelo cliente."""
    return [
        {"type": error.get("type"), "loc": list(error.get("loc", ())), "msg": error.get("msg")}
        for error in exc.errors()
    ]


async def application_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApplicationError)
    status_code = _STATUS_BY_APPLICATION_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return _problem_response(request, status_code=status_code, title=exc.title, detail=exc.detail)


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Uma violação de invariante de domínio (ex.:
    `InvalidStatusTransitionError`, RAG-010) é sempre um conflito com o
    estado atual do recurso — daí 409, não 422 (os dados em si são
    válidos; é a transição que não é permitida agora)."""
    assert isinstance(exc, DomainError)
    return _problem_response(
        request, status_code=status.HTTP_409_CONFLICT, title="Conflito de estado", detail=str(exc)
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    detail = exc.detail if isinstance(exc.detail, str) else None
    return _problem_response(
        request, status_code=exc.status_code, title=detail or "Erro HTTP", detail=detail
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return _problem_response(
        request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        title="Erro de validação",
        detail="Um ou mais campos são inválidos.",
        errors=_serialize_validation_errors(exc),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Rede de segurança final: qualquer exceção que nenhum handler
    acima tratou vira um 500 genérico. O detalhe completo vai só para o
    log do servidor (com o `request_id` para correlacionar); o cliente
    nunca vê a exceção, a mensagem ou qualquer stack trace."""
    request_id = _request_id(request)
    logger.exception("unhandled exception (request_id=%s)", request_id)
    return _problem_response(
        request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        title="Erro interno",
        detail="Ocorreu um erro inesperado.",
    )


def register_error_handlers(app: FastAPI) -> None:
    """Chamado por `create_app()` (`apps/api/main.py`)."""
    app.middleware("http")(request_id_middleware)
    app.add_exception_handler(ApplicationError, application_error_handler)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
