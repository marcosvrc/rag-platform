"""Testes de RAG-024: normalização e chunking determinístico
(`packages.ingestion.chunking`).

Cobre os critérios de aceite da atividade: defaults configuráveis por
base; um chunk nunca mistura seções (garante "não mistura documentos"
e "preserva seção" ao mesmo tempo — ver docstring do módulo); página e
origem são preservadas; testes de borda (parágrafo maior que
chunk_size, chunk final abaixo do mínimo, documento vazio, sobreposição
sem duplicação de conteúdo) passam.
"""

from __future__ import annotations

import pytest

from packages.ingestion.chunking import (
    ChunkDraft,
    ChunkingConfig,
    InvalidChunkingConfigError,
    _count_tokens,
    chunk_document,
)


def test_empty_or_blank_markdown_produces_no_chunks() -> None:
    assert chunk_document("", title="t", origin="o") == []
    assert chunk_document("   \n\n  \n", title="t", origin="o") == []


def test_single_paragraph_without_heading() -> None:
    markdown = "Isto é um parágrafo simples sem título nenhum."

    chunks = chunk_document(markdown, title="Doc", origin="doc.md")

    assert len(chunks) == 1
    chunk = chunks[0]
    assert isinstance(chunk, ChunkDraft)
    assert chunk.content == markdown
    assert chunk.section is None
    assert chunk.page is None
    assert chunk.chunk_index == 0
    assert chunk.token_count == _count_tokens(markdown)
    assert chunk.metadata == {"title": "Doc", "origin": "doc.md"}


def test_never_mixes_sections_into_the_same_chunk() -> None:
    markdown = "# Seção 1\n\nTexto da seção um.\n\n# Seção 2\n\nTexto da seção dois."

    chunks = chunk_document(markdown, title="Doc", origin="doc.md")

    assert len(chunks) == 2
    assert chunks[0].section == "Seção 1"
    assert chunks[1].section == "Seção 2"
    assert chunks[0].chunk_index == 0
    assert chunks[1].chunk_index == 1
    # nenhum chunk contém conteúdo da outra seção
    assert "seção dois" not in chunks[0].content.lower()
    assert "seção um" not in chunks[1].content.lower()


def test_content_before_first_heading_has_no_section() -> None:
    markdown = "Introdução sem título.\n\n# Primeira\n\nConteúdo 1."

    chunks = chunk_document(markdown, title="Doc", origin="doc.md")

    assert chunks[0].section is None
    assert chunks[1].section == "Primeira"


def test_page_is_always_none_for_currently_supported_formats() -> None:
    # Nenhum formato hoje suportado pelo RAG-023 (Markdown, texto puro,
    # DOCX) tem noção de página no Docling — ver docstring do módulo.
    chunks = chunk_document("# T\n\nconteúdo", title="Doc", origin="doc.md")

    assert all(chunk.page is None for chunk in chunks)


def test_chunking_is_deterministic() -> None:
    markdown = "# A\n\nTexto A.\n\n# B\n\nTexto B mais longo para variar tamanho."

    first = chunk_document(markdown, title="Doc", origin="doc.md")
    second = chunk_document(markdown, title="Doc", origin="doc.md")

    assert first == second


def test_independent_calls_do_not_leak_state_between_documents() -> None:
    # "não mistura documentos": cada chamada processa um documento só,
    # e chamadas sucessivas não podem se contaminar.
    first = chunk_document("# A\n\nConteúdo do documento A.", title="A", origin="a.md")
    second = chunk_document("# B\n\nConteúdo do documento B.", title="B", origin="b.md")

    assert "documento A" not in " ".join(c.content for c in second)
    assert "documento B" not in " ".join(c.content for c in first)
    assert first[0].metadata["origin"] == "a.md"
    assert second[0].metadata["origin"] == "b.md"


class TestChunkingConfig:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"chunk_size": 0},
            {"chunk_size": -1},
            {"chunk_overlap": -1},
            {"chunk_size": 100, "chunk_overlap": 100},
            {"chunk_size": 100, "chunk_overlap": 150},
            {"min_chunk_size": 0},
            {"chunk_size": 100, "min_chunk_size": 150},
        ],
    )
    def test_rejects_inconsistent_values(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(InvalidChunkingConfigError):
            ChunkingConfig(**kwargs)

    def test_defaults_match_plan_section_11_1(self) -> None:
        config = ChunkingConfig()

        assert config.chunk_size == 500
        assert config.chunk_overlap == 75
        assert config.min_chunk_size == 50

    def test_from_knowledge_base_config_overrides_only_recognized_keys(self) -> None:
        config = ChunkingConfig.from_knowledge_base_config(
            {"chunk_size": 200, "unrelated_setting": "ignored"}
        )

        assert config.chunk_size == 200
        assert config.chunk_overlap == 75
        assert config.min_chunk_size == 50

    def test_from_knowledge_base_config_empty_dict_uses_defaults(self) -> None:
        assert ChunkingConfig.from_knowledge_base_config({}) == ChunkingConfig()


class TestTokenFallback:
    def test_paragraph_larger_than_chunk_size_is_split_by_tokens(self) -> None:
        words = " ".join(f"palavra{i}" for i in range(1000))
        config = ChunkingConfig(chunk_size=100, chunk_overlap=20, min_chunk_size=10)

        chunks = chunk_document(words, title="Doc", origin="doc.md", config=config)

        assert len(chunks) > 1
        for chunk in chunks[:-1]:
            assert chunk.token_count == config.chunk_size
        assert chunks[-1].token_count <= config.chunk_size
        for chunk in chunks:
            assert chunk.token_count == _count_tokens(chunk.content)

    def test_consecutive_fallback_pieces_share_exactly_the_configured_overlap(self) -> None:
        words = " ".join(f"palavra{i}" for i in range(300))
        config = ChunkingConfig(chunk_size=100, chunk_overlap=20, min_chunk_size=10)

        chunks = chunk_document(words, title="Doc", origin="doc.md", config=config)

        for first, second in zip(chunks, chunks[1:], strict=False):
            first_tokens = first.content.split()
            second_tokens = second.content.split()
            assert first_tokens[-config.chunk_overlap :] == second_tokens[: config.chunk_overlap]


class TestZeroOverlap:
    def test_chunk_overlap_zero_never_repeats_content(self) -> None:
        words = " ".join(f"palavra{i}" for i in range(300))
        config = ChunkingConfig(chunk_size=100, chunk_overlap=0, min_chunk_size=10)

        chunks = chunk_document(words, title="Doc", origin="doc.md", config=config)

        assert len(chunks) > 1
        seen: set[str] = set()
        for chunk in chunks:
            tokens = set(chunk.content.split())
            assert not (tokens & seen), "tokens repetidos entre chunks com chunk_overlap=0"
            seen |= tokens

    def test_chunk_overlap_zero_trailing_merge_has_no_seed_to_drop(self) -> None:
        # Com chunk_overlap=0 todo chunk tem overlap_prefix=0 — a fusão
        # do último chunk (abaixo do mínimo) cai no caminho
        # `_drop_leading_tokens(..., count=0)`, que devolve o texto
        # inteiro sem cortar nada (não há sobreposição para remover).
        config = ChunkingConfig(chunk_size=20, chunk_overlap=0, min_chunk_size=10)
        paragraphs = [
            " ".join(f"w{i}" for i in range(18)),
            "y0 y1 y2",
        ]
        markdown = "# Sec\n\n" + "\n\n".join(paragraphs)

        chunks = chunk_document(markdown, title="Doc", origin="doc.md", config=config)

        assert len(chunks) == 1
        assert "y0 y1 y2" in chunks[0].content
        assert chunks[0].token_count == _count_tokens(chunks[0].content)


class TestMinimumChunkSize:
    def test_trailing_chunk_below_minimum_is_merged_without_duplication(self) -> None:
        # Duas seções... não, uma seção com 3 parágrafos onde o
        # empacotamento guloso deixa um resto final minúsculo.
        config = ChunkingConfig(chunk_size=20, chunk_overlap=5, min_chunk_size=10)
        paragraphs = [
            " ".join(f"w{i}" for i in range(18)),
            " ".join(f"x{i}" for i in range(18)),
            "y0 y1 y2",  # bem abaixo de min_chunk_size=10
        ]
        markdown = "# Sec\n\n" + "\n\n".join(paragraphs)

        chunks = chunk_document(markdown, title="Doc", origin="doc.md", config=config)

        assert all(chunk.section == "Sec" for chunk in chunks)
        assert "y0 y1 y2" in chunks[-1].content
        # nenhum token do fim da seção aparece duplicado pela fusão
        full_text = " ".join(chunk.content for chunk in chunks)
        assert full_text.count("x17") == 1
        for chunk in chunks:
            assert chunk.token_count == _count_tokens(chunk.content)

    def test_trailing_fallback_piece_below_minimum_is_merged_without_duplication(self) -> None:
        config = ChunkingConfig(chunk_size=10, chunk_overlap=3, min_chunk_size=5)
        # 24 tokens: janelas de 10 com passo 7 -> última janela tem 3
        # tokens (abaixo do mínimo de 5) e deve ser fundida na anterior.
        text = " ".join(f"t{i}" for i in range(24))

        chunks = chunk_document(text, title="Doc", origin="doc.md", config=config)

        assert chunks[-1].token_count >= config.min_chunk_size
        for chunk in chunks:
            assert chunk.token_count == _count_tokens(chunk.content)
        # cada token original aparece 1x (sem sobreposição) ou 2x (com
        # sobreposição legítima entre os dois chunks vizinhos), nunca 3x
        full_text = " ".join(chunk.content for chunk in chunks)
        counts = {f"t{i}": full_text.split().count(f"t{i}") for i in range(24)}
        assert all(count in (1, 2) for count in counts.values())

    def test_single_chunk_section_below_minimum_is_preserved_as_is(self) -> None:
        # Uma seção curta demais para ter para onde fundir: não pode
        # cruzar para outra seção nem descartar conteúdo.
        config = ChunkingConfig(chunk_size=100, chunk_overlap=10, min_chunk_size=50)
        markdown = "# Sec\n\ncurto."

        chunks = chunk_document(markdown, title="Doc", origin="doc.md", config=config)

        assert len(chunks) == 1
        assert "curto." in chunks[0].content
        assert chunks[0].token_count < config.min_chunk_size
