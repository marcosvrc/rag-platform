"""Porta de parsing de conteúdo de documentos (RAG-023).

Ponto de extensão entre o pipeline de indexação (RAG-024 em diante:
normalização, chunking, embeddings, persistência) e a biblioteca
concreta usada para extrair texto de um documento. O domínio e os
casos de uso não devem importar Docling (nem qualquer outra biblioteca
de parsing) diretamente — só esta interface (seção 5.1 do plano: regra
já seguida por `ObjectStoragePort`, `JobQueuePort` etc.).

Cobre os quatro tipos aceitos no upload (RAG-021,
`packages.application.commands.document._ALLOWED_EXTENSIONS_BY_MIME_TYPE`):
PDF, Markdown, texto puro e DOCX. Implementações não são obrigadas a
suportar os quatro com sucesso — devem levantar `UnsupportedDocumentFormatError`
para qualquer `content_type` que não conseguam processar, permitindo
que um formato seja adicionado depois sem mudar esta porta (é o caso
do adapter Docling desta atividade: veja `adapters/docling/parser.py`
sobre por que PDF levanta esse erro por enquanto)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class DocumentParsingError(Exception):
    """Categoria base: o parser reconhece `content_type`, mas não
    conseguiu extrair conteúdo do arquivo fornecido (conteúdo
    corrompido, malformado, ou qualquer outra falha do parser).

    `content_type` e `detail` seguem o mesmo contrato de
    `ApplicationError` (`packages.application.errors`): `detail` é
    seguro para o chamador expor (nunca deve conter dados sensíveis
    nem stack traces internos da biblioteca de parsing)."""

    def __init__(self, *, content_type: str, detail: str) -> None:
        self.content_type = content_type
        self.detail = detail
        super().__init__(f"falha ao extrair conteúdo ({content_type}): {detail}")


class UnsupportedDocumentFormatError(DocumentParsingError):
    """`content_type` não é suportado por esta implementação — nunca
    (formato realmente desconhecido) ou ainda não (ex.: PDF no adapter
    Docling desta atividade, que depende de um modelo de layout não
    provisionado; ver `adapters/docling/parser.py`)."""


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Resultado de uma extração bem-sucedida.

    `markdown` é o texto extraído, normalizado para Markdown (é o que
    RAG-024 vai normalizar/chunkar — Markdown preserva estrutura de
    títulos/seções o suficiente para chunking determinístico sem
    amarrar o restante do pipeline ao modelo de documento interno do
    Docling). `page_count` é `None` quando o formato de origem não tem
    noção de página (Markdown, texto puro, DOCX sem paginação nativa)."""

    markdown: str
    page_count: int | None
    original_mimetype: str


class DocumentParserPort(ABC):
    """Extrai texto e metadados de um documento já em memória (os
    bytes já foram baixados do object storage pelo chamador — esta
    porta não conhece `ObjectStoragePort`)."""

    @abstractmethod
    async def parse(self, *, filename: str, content: bytes, content_type: str) -> ParsedDocument:
        """Extrai `content` (tipo `content_type`, nome original
        `filename` — usado só para diagnóstico/logs, não para decidir o
        formato) para um `ParsedDocument`.

        Levanta `UnsupportedDocumentFormatError` se `content_type` não
        for suportado por esta implementação, ou `DocumentParsingError`
        (ou uma subclasse mais específica) se o parsing falhar por
        qualquer outro motivo."""
