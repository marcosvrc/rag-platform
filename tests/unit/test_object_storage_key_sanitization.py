"""Testes de RAG-020: `sanitize_object_key` (invariante "nomes são
sanitizados" do aceite da atividade)."""

import pytest

from packages.application.ports.object_storage import (
    InvalidObjectKeyError,
    sanitize_object_key,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("relatorio.pdf", "relatorio.pdf"),
        ("pasta/arquivo.txt", "pasta/arquivo.txt"),
        ("nome com espaços.pdf", "nome_com_espaços.pdf"),
        ("nome__com___repetidos.pdf", "nome_com_repetidos.pdf"),
        ("café com açúcar.pdf", "café_com_açúcar.pdf"),
        ("a/b/../c.pdf", "a/b/c.pdf"),
        ("./relativo.pdf", "relativo.pdf"),
        ("//dupla/barra.pdf", "dupla/barra.pdf"),
        ("-inicia-com-separador.pdf", "inicia-com-separador.pdf"),
        ("termina-com-separador-.pdf", "termina-com-separador_pdf"),
        ('nome"com<caracteres>ruins?.pdf', "nome_com_caracteres_ruins_pdf"),
    ],
)
def test_sanitize_object_key_produces_the_expected_key(name: str, expected: str) -> None:
    assert sanitize_object_key(name) == expected


def test_sanitize_object_key_is_deterministic() -> None:
    name = "Relatório Financeiro (Q3)/versão final.pdf"
    assert sanitize_object_key(name) == sanitize_object_key(name)


@pytest.mark.parametrize("name", ["", "..", ".", "/", "///", "../../.."])
def test_sanitize_object_key_rejects_names_with_no_usable_component(name: str) -> None:
    with pytest.raises(InvalidObjectKeyError):
        sanitize_object_key(name)


def test_sanitize_object_key_rejects_keys_over_the_size_limit() -> None:
    too_long = "a" * 2000 + ".pdf"
    with pytest.raises(InvalidObjectKeyError):
        sanitize_object_key(too_long)


def test_sanitize_object_key_never_lets_a_traversal_segment_survive() -> None:
    # Mesmo formas menos óbvias de ".." não devem sobreviver à sanitização.
    key = sanitize_object_key("../../etc/passwd")
    assert ".." not in key.split("/")
