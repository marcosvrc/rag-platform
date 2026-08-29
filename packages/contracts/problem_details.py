"""Contrato de erro da API (RAG-013): Problem Details, RFC 7807, exigido
pela seção 8 do plano ("Erros no formato Problem Details
(`application/problem+json`)").

`request_id` é a extensão de correlação do plano ("Logs JSON com
trace_id, tenant_id, request_id e job_id quando disponíveis") — é
distinto do `trace_id` de tracing distribuído (OpenTelemetry, RAG-052,
ainda não implementado): `request_id` é atribuído por
`apps/api/errors.py` a cada requisição HTTP, sem depender de nenhuma
infraestrutura de tracing.
"""

from typing import Any

from pydantic import BaseModel, Field


class ProblemDetail(BaseModel):
    """RFC 7807. `type`/`title`/`status`/`detail`/`instance` são os
    membros padrão; `request_id` e `errors` são extensões deste projeto."""

    model_config = {"extra": "forbid"}

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str
    errors: list[dict[str, Any]] | None = Field(
        default=None,
        description="Detalhe por campo de uma falha de validação (422); ausente nos demais erros.",
    )
