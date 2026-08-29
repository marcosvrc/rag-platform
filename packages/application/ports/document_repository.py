"""Porta do repositório de documentos (RAG-021/RAG-022).

Cria `Document` + `DocumentVersion` (v1) + `IndexJob` (INDEX, PENDING)
juntos, na mesma transação lógica (seção 11, passo 5 do plano: "criar
documento, versão e job na mesma transação lógica") — por isso é um
único método (`create_document`), não três chamadas separadas a três
repositórios.

Todo método que recebe `tenant_id` o recebe explicitamente, mesmo
princípio de `KnowledgeBaseRepositoryPort` (RAG-012): nenhuma consulta
de negócio acontece sem esse filtro. Os três métodos de ciclo de vida
do `IndexJob` (`claim_index_job`/`mark_index_job_succeeded`/
`mark_index_job_failed`, RAG-022) são a exceção: são chamados pelo
worker a partir só do `index_job_id` (a mensagem da fila carrega só o
id, ver `JobQueuePort`), um contexto interno sem tenant autenticado —
não expostos a nenhum tenant diretamente (isso é RAG-027, endpoint
`GET /v1/jobs/{id}`, que fará o isolamento por tenant na hora de expor
o status ao cliente).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from uuid import UUID

from packages.domain.entities.document import Document
from packages.domain.entities.document_version import DocumentVersion
from packages.domain.entities.index_job import IndexJob


class DocumentChecksumConflictError(Exception):
    """Já existe um documento não excluído com este checksum nesta base
    (unique constraint `knowledge_base_id` + `checksum`, RAG-011) — a
    detecção de duplicidade exigida pelo critério de aceite desta
    atividade."""

    def __init__(self, *, knowledge_base_id: UUID, existing_document_id: UUID) -> None:
        self.knowledge_base_id = knowledge_base_id
        self.existing_document_id = existing_document_id
        super().__init__(
            f"Já existe um documento ({existing_document_id}) com este conteúdo "
            f"(checksum) na base {knowledge_base_id}."
        )


class IdempotencyKeyConflictError(Exception):
    """A mesma `Idempotency-Key` já foi usada nesta base para uma
    requisição com nome, tipo ou conteúdo diferentes.

    Só é levantada em uma corrida genuína (duas requisições
    concorrentes com a mesma chave, ambas passando pela checagem
    prévia antes que qualquer uma tivesse persistido algo) — o caminho
    comum (repetição sequencial da mesma chave) é resolvido como uma
    repetição válida, não como conflito (ver `DocumentUpload.replayed`).
    """

    def __init__(self, *, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(
            f"Idempotency-Key {idempotency_key!r} já foi usada para uma requisição diferente."
        )


@dataclass(frozen=True, slots=True)
class DocumentUpload:
    """Resultado de uma criação (ou repetição idempotente) de documento."""

    document: Document
    version: DocumentVersion
    index_job: IndexJob
    replayed: bool
    """`True` quando este resultado veio de uma `Idempotency-Key` já
    usada antes (nada novo foi criado nesta chamada)."""


class DocumentRepositoryPort(ABC):
    """Porta hexagonal (seção 5.1 do plano): a camada de aplicação só
    conhece esta interface, nunca SQLAlchemy diretamente."""

    @abstractmethod
    async def find_by_checksum(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, checksum: str
    ) -> Document | None:
        """Documento não excluído com este checksum nesta base, ou
        `None`. Usado para detectar duplicidade antes de armazenar o
        arquivo (seção 11, passo 3 do plano) — verificação em melhor
        esforço; `create_document` também aplica a unique constraint
        do banco como defesa contra corrida."""

    @abstractmethod
    async def find_idempotent_upload(
        self, *, tenant_id: UUID, knowledge_base_id: UUID, idempotency_key: str
    ) -> DocumentUpload | None:
        """Resultado de uma criação anterior com esta `Idempotency-Key`
        nesta base, ou `None` se a chave nunca foi usada aqui. Não
        compara nome/tipo/checksum contra a requisição atual — quem
        chama (`packages.application.commands.document`) decide se é
        uma repetição válida ou um conflito."""

    @abstractmethod
    async def create_document(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        name: str,
        mime_type: str,
        checksum: str,
        object_key: str,
        idempotency_key: str | None,
    ) -> DocumentUpload:
        """Cria `Document` (status `PENDING`) + `DocumentVersion`
        (versão 1) + `IndexJob` (tipo `INDEX`, status `PENDING`).

        Se `idempotency_key` for informada, também registra o mapeamento
        (ver `adapters/postgres/models/document_idempotency_key.py`).

        Levanta `DocumentChecksumConflictError` se já existir um
        documento não excluído com o mesmo checksum nesta base.
        Levanta `IdempotencyKeyConflictError` no caso raro de corrida
        descrito na classe.
        """

    @abstractmethod
    async def claim_index_job(self, *, index_job_id: UUID) -> IndexJob | None:
        """Reivindica `index_job_id` para processamento: transição
        atômica `PENDING -> RUNNING` — o "lock idempotente" do passo 7
        (seção 11 do plano). Devolve o `IndexJob` já em `RUNNING` se a
        reivindicação teve sucesso, ou `None` se o job não existe ou já
        não está mais em `PENDING` (reivindicado por outro worker, ou
        já terminal) — nesse caso, quem chama não deve processá-lo."""

    @abstractmethod
    async def mark_index_job_succeeded(self, *, index_job_id: UUID) -> None:
        """Marca `index_job_id` como `SUCCEEDED`."""

    @abstractmethod
    async def mark_index_job_failed(
        self,
        *,
        index_job_id: UUID,
        attempts: int,
        error_code: str,
        error_message: str,
        final: bool,
    ) -> None:
        """Registra uma tentativa falha de `index_job_id`: atualiza
        `attempts`/`error_code`/`error_message`. Se `final=True`, marca
        `status=FAILED` (falha definitiva — critério de aceite do
        RAG-022); caso contrário, mantém `status=RUNNING` (o Celery
        ainda vai reagendar esta mesma tentativa lógica com backoff
        exponencial — não há necessidade de reivindicar o job de novo,
        `claim_index_job` só é chamado na primeira tentativa)."""
