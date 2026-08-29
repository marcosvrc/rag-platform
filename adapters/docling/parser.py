"""Adapter de extração de conteúdo de documentos usando Docling (RAG-023).

Implementa `DocumentParserPort` para Markdown, texto puro e DOCX.

## Por que PDF não está incluído

O pipeline padrão do Docling para PDF (`StandardPdfPipeline`) baixa
pesos de modelo em runtime: um modelo de detecção de layout do Hugging
Face Hub (`huggingface_hub.snapshot_download`), sempre, e um modelo de
OCR (RapidOCR) do ModelScope quando `do_ocr=True`. Isso vale mesmo com
`do_ocr=False` — testado durante esta atividade: desabilitar OCR evita
só o segundo download, não o primeiro.

O plano (seção 4.2) já exclui "OCR avançado" do escopo do POC, mas o
download do modelo de layout não é OCR — é a extração de estrutura em
si, então não dá para simplesmente desligá-lo. E nem o ambiente de dev
local (sandbox usado para validar esta atividade) nem o container onde
esta implementação foi pesquisada têm egress liberado para
`huggingface.co`/`modelscope.cn` hoje.

Por isso este adapter instala só os extras leves do Docling
(`docling-slim[convert-core,format-markdown,format-docx,format-pdf]`,
ver `pyproject.toml`) — o extra `format-pdf` é necessário mesmo sem
usar PDF porque `docling.document_converter` importa o backend de PDF
incondicionalmente no nível de módulo, mas o `DocumentConverter` deste
adapter nunca é configurado com `InputFormat.PDF`, então nenhum modelo
de ML é carregado nem baixado. O footprint da instalação cai de ~5,5GB
(extra `standard`, que inclui `torch`/`transformers`/`docling-ibm-models`)
para ~450MB.

`content_type == "application/pdf"` levanta `UnsupportedDocumentFormatError`
com uma mensagem explicando isso — é uma limitação temporária, não uma
decisão definitiva de excluir PDF do produto. Para habilitar PDF depois:
1. Rodar `docling-tools models download` (ou
   `huggingface_hub.snapshot_download` diretamente) uma vez, num
   ambiente com egress para o Hugging Face Hub, para obter os pesos do
   modelo de layout.
2. Apontar `PdfPipelineOptions(artifacts_path=..., do_ocr=False)` para
   esse diretório pré-baixado (evita qualquer download em runtime) —
   ver `docling.datamodel.pipeline_options.PdfPipelineOptions`.
3. Adicionar o extra `models-local` (`docling-ibm-models`,
   `torch`/`torchvision`, ~5GB) à dependência e registrar
   `InputFormat.PDF` no `DocumentConverter` deste adapter.
4. Decidir onde os pesos pré-baixados vivem em cada ambiente (imagem
   Docker do worker, volume, etc.) — é uma decisão de deploy (RAG-07x),
   não de código.
"""

from __future__ import annotations

import asyncio
import io

from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.document_converter import DocumentConverter
from docling.exceptions import BaseError as DoclingBaseError
from docling_core.types.io import DocumentStream

from packages.application.ports.document_parser import (
    DocumentParserPort,
    DocumentParsingError,
    ParsedDocument,
    UnsupportedDocumentFormatError,
)

# text/plain não tem um InputFormat próprio no Docling — texto puro é
# markdown trivial (sem sintaxe especial), então é tratado pelo mesmo
# backend de Markdown. Confirmado nesta atividade: um .txt convertido
# assim produz `ConversionStatus.SUCCESS` e `origin.mimetype ==
# "text/markdown"`.
_CONTENT_TYPE_TO_FORMAT: dict[str, InputFormat] = {
    "text/markdown": InputFormat.MD,
    "text/plain": InputFormat.MD,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": InputFormat.DOCX,
}

_PDF_NOT_YET_SUPPORTED_DETAIL = (
    "extração de PDF ainda não habilitada: o pipeline de PDF do Docling "
    "depende de um modelo de layout baixado em runtime do Hugging Face Hub, "
    "indisponível neste ambiente. Ver docstring de "
    "adapters.docling.parser para o caminho de habilitação."
)


class DoclingDocumentParser(DocumentParserPort):
    """Extrai texto e metadados via Docling para Markdown, texto puro
    e DOCX. PDF levanta `UnsupportedDocumentFormatError` — ver
    docstring do módulo."""

    def __init__(self) -> None:
        self._converter = DocumentConverter(
            allowed_formats=[InputFormat.MD, InputFormat.DOCX],
        )

    async def parse(self, *, filename: str, content: bytes, content_type: str) -> ParsedDocument:
        input_format = _CONTENT_TYPE_TO_FORMAT.get(content_type)
        if input_format is None:
            detail = (
                _PDF_NOT_YET_SUPPORTED_DETAIL
                if content_type == "application/pdf"
                else f"tipo de conteúdo não suportado por este parser: {content_type!r}."
            )
            raise UnsupportedDocumentFormatError(content_type=content_type, detail=detail)

        stream = DocumentStream(name=filename, stream=io.BytesIO(content))
        try:
            # `raises_on_error=False`: uma falha de parsing vira um
            # `ConversionResult` com status/erros (checado abaixo), em
            # vez de levantar — deixa as duas categorias de erro desta
            # porta claramente distintas: `DoclingBaseError` aqui é só
            # para falhas que nem chegam a produzir um resultado (ex.:
            # `SecurityError`, `AcceleratorDeviceNotAvailableError`).
            # Docling é síncrono/bloqueante; roda em thread para não
            # travar o event loop do worker (mesmo padrão de qualquer
            # chamada bloqueante dentro de um adapter async).
            result = await asyncio.to_thread(self._converter.convert, stream, raises_on_error=False)
        except DoclingBaseError as exc:
            raise DocumentParsingError(content_type=content_type, detail=str(exc)) from exc

        if result.status != ConversionStatus.SUCCESS:
            reasons = "; ".join(str(error) for error in result.errors)
            raise DocumentParsingError(
                content_type=content_type,
                detail=reasons or f"conversão falhou com status {result.status}.",
            )

        document = result.document
        return ParsedDocument(
            markdown=document.export_to_markdown(),
            page_count=document.num_pages() or None,
            original_mimetype=content_type,
        )
