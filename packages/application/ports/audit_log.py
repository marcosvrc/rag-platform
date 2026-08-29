"""Porta de auditoria de ações administrativas (RAG-054).

Objetivo do plano: "registrar ações administrativas e acesso
relevante" com "ator, tenant, ação, recurso e timestamp", append-only.
Escopo desta atividade: as ações administrativas que já existem hoje
na API — criar/atualizar/excluir base de conhecimento (RAG-012) e
enviar/reindexar documento (RAG-021/RAG-027). Um endpoint de leitura
do trilho de auditoria (para um painel administrativo, por exemplo)
não faz parte do aceite ("registrar", não "consultar") e fica para uma
atividade futura — por isso esta porta só tem `record`, nenhum método
de consulta.

Append-only por design, não por convenção: a porta não declara nenhum
método de atualização ou remoção, e nenhum adapter (`adapters/audit_log/`)
implementa um. `AuditEvent` é imutável (`frozen=True`) pelo mesmo
motivo das entidades de domínio (RAG-010) — ainda que não seja uma,
ver `adapters/postgres/models/audit_event.py` sobre por que fica fora
de `packages/domain/entities` (mesmo precedente de
`document_idempotency_keys`, RAG-021: infraestrutura de aplicação, não
um conceito do domínio de RAG).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Um evento de auditoria já registrado (retornado por
    `AuditLogPort.record`, principalmente para os adapters em memória
    de teste inspecionarem o que foi gravado)."""

    id: UUID
    tenant_id: UUID
    actor: str
    action: str
    resource_type: str
    resource_id: UUID
    occurred_at: datetime


class AuditLogPort(ABC):
    """Registra eventos de auditoria. Todo adapter
    (`adapters/audit_log/`) implementa isso; nenhum deles falha a
    ação que está sendo auditada por um erro de auditoria (ver cada
    adapter sobre como trata isso)."""

    @abstractmethod
    async def record(
        self,
        *,
        tenant_id: UUID,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: UUID,
    ) -> AuditEvent:
        """Registra um evento de auditoria e devolve a entidade
        criada (com `id`/`occurred_at` atribuídos pelo adapter — quem
        chama nunca escolhe o timestamp, é sempre "agora")."""


async def record_audit_event_safely(
    audit_log: AuditLogPort,
    *,
    tenant_id: UUID,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: UUID,
) -> None:
    """Chamado pelos routers (`apps/api/routers/knowledge_bases.py`,
    `apps/api/routers/documents.py`) em vez de `audit_log.record(...)`
    direto: uma falha ao registrar auditoria (ex.: banco indisponível)
    nunca deve derrubar a ação administrativa que já teve sucesso —
    isso trocaria uma falha de observabilidade por uma indisponibilidade
    real da API. A falha é logada (nunca engolida em silêncio) para que
    uma lacuna na trilha de auditoria continue visível na operação.
    """
    try:
        await audit_log.record(
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    except Exception:  # ver docstring: nunca deve propagar (não é um bug engolido).
        _logger.exception(
            "Falha ao registrar evento de auditoria (action=%s, resource_type=%s, "
            "resource_id=%s, tenant_id=%s) — a ação em si foi concluída normalmente.",
            action,
            resource_type,
            resource_id,
            tenant_id,
        )
