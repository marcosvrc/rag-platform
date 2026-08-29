"""Normalização e chunking determinístico de documentos (RAG-024).

Implementa os passos 9-10 do fluxo de indexação (seção 11 do plano):
"normalizar texto sem eliminar estrutura semântica" e "dividir por
seções e parágrafos; usar tokens como fallback". A extração em si
(RAG-023, `DoclingDocumentParser`) já normaliza para Markdown
preservando títulos/seções — este módulo consome esse Markdown e o
divide em pedaços do tamanho de um `Chunk`, seguindo os defaults da
seção 11.1 (tamanho 500 tokens, sobreposição 75, mínimo 50).

`chunk_document()` não persiste nada (RAG-026) nem gera embeddings
(RAG-025): a saída é uma lista de `ChunkDraft`, um valor sem `id`/
`tenant_id`/`knowledge_base_id`/`version_id`/`embedding` — esses campos
só existem quando o chunk é persistido, quando RAG-026 monta um
`Chunk` de domínio a partir de um `ChunkDraft` mais o contexto do job.
Cada chamada processa exatamente um documento (a assinatura da função
não aceita mais de um Markdown por vez) — "não misturar documentos" é
garantido estruturalmente, não por convenção.

## Seções e parágrafos

O documento é dividido em blocos por título Markdown (`#`..`######`) e
por parágrafo (linhas separadas por linha em branco). Blocos
consecutivos da mesma seção são empacotados gulosamente em um chunk até
`chunk_size` tokens; um chunk nunca combina blocos de seções diferentes
(é o que garante "preserva seção" de forma inequívoca — cada chunk tem
exatamente uma seção de origem). Um parágrafo isolado maior que
`chunk_size` é dividido por tokens diretamente (o fallback do passo
10), com sobreposição de `chunk_overlap` tokens entre os pedaços.

Um parágrafo nunca é quebrado no meio só para caber exatamente em
`chunk_size` — um chunk combina parágrafos inteiros até o limite, então
pode terminar um pouco acima de `chunk_size` para acomodar o próximo
parágrafo inteiro (só o fallback por tokens acima respeita o limite à
risca, porque aí não há mais parágrafo para preservar). `chunk_size` é
o gatilho para fechar um chunk, não um teto rígido de conteúdo.

## Página

Nenhum dos formatos suportados hoje pelo RAG-023 (Markdown, texto puro,
DOCX) tem noção de página no modelo do Docling (`num_pages() == 0`) —
só PDF teria, e PDF ainda não é extraído (ver `adapters/docling/parser.py`).
Por isso `ChunkDraft.page` é sempre `None` na prática atual; o campo
existe e é propagado de ponta a ponta para quando a extração de PDF
paginada existir, sem precisar mudar esta assinatura.

## Contagem de tokens

Não usamos um tokenizer real (ex.: `tiktoken`): como o Docling
(RAG-023), o `tiktoken` baixa o vocabulário BPE em runtime na primeira
chamada (`openaipublic.blob.core.windows.net`) — o mesmo problema de
egress já documentado para o modelo de layout do pipeline de PDF do
Docling. Em vez disso, `_count_tokens` aproxima com uma contagem de
palavras/pontuação (regex, sem download, determinística). É uma
aproximação, não a contagem exata que um modelo de embeddings real
usaria — aceitável aqui, onde o que importa é um tamanho de chunk
consistente e determinístico, não uma contagem exata. Vale reavaliar
antes de reusar esse número para orçamento de contexto de geração
(RAG-041) ou para limites de lote de uma API de embeddings (RAG-025):
nesse ponto, considerar pré-cachear o vocabulário de um tokenizer real
(mesma solução cogitada para o modelo de layout do Docling — baixar
uma vez com egress liberado, apontar para um cache local).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import groupby
from typing import Any

_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.*)$")

_DEFAULT_CHUNK_SIZE = 500
_DEFAULT_CHUNK_OVERLAP = 75
_DEFAULT_MIN_CHUNK_SIZE = 50


class InvalidChunkingConfigError(ValueError):
    """`ChunkingConfig` recebeu valores inconsistentes entre si."""


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Parâmetros de chunking (seção 11.1 do plano). Configurável por
    base via `from_knowledge_base_config` — é o que torna os defaults
    "configuráveis por base" (critério de aceite do RAG-024)."""

    chunk_size: int = _DEFAULT_CHUNK_SIZE
    chunk_overlap: int = _DEFAULT_CHUNK_OVERLAP
    min_chunk_size: int = _DEFAULT_MIN_CHUNK_SIZE

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise InvalidChunkingConfigError("chunk_size deve ser positivo.")
        if self.chunk_overlap < 0:
            raise InvalidChunkingConfigError("chunk_overlap não pode ser negativo.")
        if self.chunk_overlap >= self.chunk_size:
            raise InvalidChunkingConfigError("chunk_overlap deve ser menor que chunk_size.")
        if self.min_chunk_size <= 0:
            raise InvalidChunkingConfigError("min_chunk_size deve ser positivo.")
        if self.min_chunk_size > self.chunk_size:
            raise InvalidChunkingConfigError("min_chunk_size não pode exceder chunk_size.")

    @classmethod
    def from_knowledge_base_config(cls, config: dict[str, Any]) -> ChunkingConfig:
        """Constrói a partir de `KnowledgeBase.config` (RAG-010),
        usando os defaults da seção 11.1 para as chaves ausentes.
        Chaves reconhecidas: `chunk_size`, `chunk_overlap`,
        `min_chunk_size` — qualquer outra chave em `config` é
        ignorada (esse dict serve outras atividades também)."""
        kwargs: dict[str, int] = {
            key: config[key]
            for key in ("chunk_size", "chunk_overlap", "min_chunk_size")
            if key in config
        }
        return cls(**kwargs)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """Um chunk ainda não persistido — falta tudo que só existe depois
    da persistência (`id`, `tenant_id`, `knowledge_base_id`,
    `version_id`, `embedding`; ver `packages.domain.entities.chunk.Chunk`)."""

    content: str
    token_count: int
    chunk_index: int
    section: str | None
    page: int | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Block:
    section: str | None
    text: str


def _tokenize(text: str) -> list[re.Match[str]]:
    return list(_TOKEN_PATTERN.finditer(text))


def _count_tokens(text: str) -> int:
    return len(_tokenize(text))


def _take_overlap(text: str, overlap_tokens: int) -> str:
    """Últimos `overlap_tokens` tokens de `text`, como substring
    original (preserva espaçamento/pontuação exatos — não é uma
    rejunção de tokens)."""
    if overlap_tokens <= 0:
        return ""
    tokens = _tokenize(text)
    if not tokens:
        return ""
    start_index = max(0, len(tokens) - overlap_tokens)
    return text[tokens[start_index].start() :]


def _drop_leading_tokens(text: str, count: int) -> str:
    """Complemento de `_take_overlap`: remove os primeiros `count`
    tokens de `text`. Usado por `_enforce_minimum` para não duplicar a
    sobreposição já presente no início de um chunk ao fundi-lo com o
    anterior (ver docstring de `_enforce_minimum`)."""
    if count <= 0:
        return text
    tokens = _tokenize(text)
    if count >= len(tokens):
        return ""
    return text[tokens[count].start() :]


def _split_into_blocks(markdown: str) -> list[_Block]:
    """Divide `markdown` em títulos e parágrafos, em ordem, cada um
    marcado com a seção (título) mais recente."""
    blocks: list[_Block] = []
    current_section: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if not current_lines:
            return
        text = "\n".join(current_lines).strip()
        if text:
            blocks.append(_Block(section=current_section, text=text))
        current_lines.clear()

    for line in markdown.splitlines():
        heading_match = _HEADING_PATTERN.match(line)
        if heading_match is not None:
            flush()
            current_section = heading_match.group(2).strip()
            blocks.append(_Block(section=current_section, text=line.strip()))
            continue
        if line.strip() == "":
            flush()
            continue
        current_lines.append(line)
    flush()
    return blocks


def _split_by_tokens(text: str, config: ChunkingConfig) -> list[tuple[str, int, int]]:
    """Fallback do passo 10: divide um bloco maior que `chunk_size`
    diretamente por tokens, com sobreposição de `chunk_overlap` entre
    pedaços consecutivos. Retorna `(content, token_count,
    overlap_prefix_tokens)` — o terceiro elemento é quantos tokens no
    início do pedaço são repetição do pedaço anterior (0 para o
    primeiro, `chunk_overlap` para os demais; ver `_enforce_minimum`)."""
    tokens = _tokenize(text)
    pieces: list[tuple[str, int, int]] = []
    total = len(tokens)
    step = config.chunk_size - config.chunk_overlap
    start = 0
    while start < total:
        end = min(start + config.chunk_size, total)
        piece_text = text[tokens[start].start() : tokens[end - 1].end()]
        overlap_prefix = 0 if start == 0 else config.chunk_overlap
        pieces.append((piece_text, end - start, overlap_prefix))
        if end >= total:
            break
        start += step
    return pieces


def _pack_section(blocks: list[_Block], config: ChunkingConfig) -> list[tuple[str, int]]:
    """Empacota gulosamente os blocos de UMA seção em chunks de até
    `chunk_size` tokens, com sobreposição entre chunks consecutivos."""
    # (content, token_count, overlap_prefix_tokens) — o terceiro
    # elemento é bookkeeping interno para `_enforce_minimum` não
    # duplicar a sobreposição ao fundir; não sai deste módulo.
    chunks: list[tuple[str, int, int]] = []
    current_parts: list[str] = []
    current_tokens = 0
    current_overlap_prefix = 0

    def flush() -> None:
        nonlocal current_parts, current_tokens, current_overlap_prefix
        if current_parts:
            chunks.append(("\n\n".join(current_parts), current_tokens, current_overlap_prefix))
        current_parts = []
        current_tokens = 0
        current_overlap_prefix = 0

    for block in blocks:
        block_tokens = _count_tokens(block.text)

        if block_tokens > config.chunk_size:
            flush()
            chunks.extend(_split_by_tokens(block.text, config))
            continue

        if current_parts and current_tokens + block_tokens > config.chunk_size:
            flush()
            overlap_text = _take_overlap(chunks[-1][0], config.chunk_overlap)
            if overlap_text:
                overlap_tokens = _count_tokens(overlap_text)
                current_parts = [overlap_text]
                current_tokens = overlap_tokens
                current_overlap_prefix = overlap_tokens

        current_parts.append(block.text)
        current_tokens += block_tokens

    flush()
    return _enforce_minimum(chunks, config)


def _enforce_minimum(
    chunks: list[tuple[str, int, int]], config: ChunkingConfig
) -> list[tuple[str, int]]:
    """Se o último chunk de uma seção ficou abaixo de `min_chunk_size`,
    funde no chunk anterior DA MESMA SEÇÃO (nunca descarta conteúdo,
    nunca cruza seção). Uma seção com um único chunk abaixo do mínimo
    fica como está — não há para onde fundir sem violar "preserva
    seção".

    Antes de concatenar, remove do chunk fundido os `overlap_prefix_tokens`
    iniciais: esse chunk já começa com uma cópia da sobreposição do
    chunk anterior (ver `_pack_section`), então concatenar os dois
    textos crus duplicaria esse trecho — a sobreposição só deve
    aparecer uma vez depois da fusão."""
    if len(chunks) < 2:
        return [(content, tokens) for content, tokens, _ in chunks]

    last_content, last_tokens, last_overlap_prefix = chunks[-1]
    if last_tokens >= config.min_chunk_size:
        return [(content, tokens) for content, tokens, _ in chunks]

    prev_content, prev_tokens, prev_overlap_prefix = chunks[-2]
    deduped_content = _drop_leading_tokens(last_content, last_overlap_prefix)
    deduped_tokens = last_tokens - last_overlap_prefix

    merged_content = f"{prev_content}\n\n{deduped_content}" if deduped_content else prev_content
    merged: list[tuple[str, int, int]] = [
        *chunks[:-2],
        (merged_content, prev_tokens + deduped_tokens, prev_overlap_prefix),
    ]
    return [(content, tokens) for content, tokens, _ in merged]


def chunk_document(
    markdown: str,
    *,
    title: str,
    origin: str,
    config: ChunkingConfig | None = None,
) -> list[ChunkDraft]:
    """Divide o Markdown extraído de um documento em `ChunkDraft`s
    determinísticos.

    `title` e `origin` (ex.: nome do arquivo original) são propagados
    para `ChunkDraft.metadata` de todo chunk — é o que preserva
    "título... e origem" do critério de aceite (seção/página já são
    campos dedicados, ver docstring do módulo). `config` usa os
    defaults da seção 11.1 se omitido.

    Um Markdown vazio (ou só espaços) produz `[]` — não é um erro,
    documentos sem conteúdo extraído simplesmente não geram chunks."""
    resolved_config = config or ChunkingConfig()
    if not markdown.strip():
        return []

    blocks = _split_into_blocks(markdown)
    metadata = {"title": title, "origin": origin}

    drafts: list[ChunkDraft] = []
    for section, section_blocks in groupby(blocks, key=lambda block: block.section):
        for content, token_count in _pack_section(list(section_blocks), resolved_config):
            drafts.append(
                ChunkDraft(
                    content=content,
                    token_count=token_count,
                    chunk_index=len(drafts),
                    section=section,
                    page=None,
                    metadata=dict(metadata),
                )
            )
    return drafts
