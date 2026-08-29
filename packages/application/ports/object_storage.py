"""Porta de object storage (RAG-020).

Interface que todo adapter de armazenamento de objetos deve implementar
(MinIO local hoje; qualquer backend compatível com S3 em produção — a
seção 5 do plano já modela isso como "MinIO local; interface compatível
com S3", ou seja, uma única porta para os dois). O domínio e os casos de
uso dependem só desta interface, nunca de um SDK de storage concreto
(seção 5.1: "O domínio não pode importar diretamente [...] MinIO").

`sanitize_object_key` também mora aqui (não em um adapter): decidir o
que é uma key segura é uma regra da aplicação, independente de qual
backend a implementa.
"""

import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass

# \w é Unicode por padrão em `str` (Python 3): cobre letras acentuadas
# (nomes em português são o caso comum aqui), não só ASCII.
_UNSAFE_CHARS = re.compile(r"[^\w.-]", re.UNICODE)
_REPEATED_SEPARATORS = re.compile(r"[._-]{2,}")
_MAX_KEY_BYTES = 1024  # limite de key do S3/MinIO


class InvalidObjectKeyError(ValueError):
    """`name` não produz uma key não vazia (ou dentro do limite de
    tamanho) depois de sanitizada."""


def sanitize_object_key(name: str) -> str:
    """Deriva uma key de object storage segura a partir de um nome
    fornecido pelo usuário (ex.: o nome de um arquivo enviado).

    - Normaliza unicode (NFKC).
    - Remove segmentos de path traversal (`.`, `..`) e barras vazias —
      cada segmento entre `/` é sanitizado independentemente.
    - Troca qualquer caractere fora de `[A-Za-z0-9._-]` por `_`.
    - Colapsa `._-` repetidos (inclusive quando um caractere inseguro
      vira `_` bem ao lado de um separador já existente, ex.:
      `"nome-.pdf"` -> `"nome_pdf"`, não `"nome-.pdf"` — o ponto da
      extensão pode ser absorvido nesse caso raro) e remove esses
      caracteres das pontas de cada segmento.
    - Levanta `InvalidObjectKeyError` se o resultado ficar vazio ou
      exceder 1024 bytes (limite do S3/MinIO).

    É determinística: o mesmo `name` sempre sanitiza para a mesma key.
    """
    normalized = unicodedata.normalize("NFKC", name)
    raw_segments = [segment for segment in normalized.split("/") if segment not in ("", ".", "..")]
    if not raw_segments:
        raise InvalidObjectKeyError(f"nome sem componentes utilizáveis: {name!r}")

    sanitized_segments = []
    for segment in raw_segments:
        cleaned = _UNSAFE_CHARS.sub("_", segment)
        cleaned = _REPEATED_SEPARATORS.sub("_", cleaned).strip("._-")
        sanitized_segments.append(cleaned or "_")

    key = "/".join(sanitized_segments)
    if len(key.encode("utf-8")) > _MAX_KEY_BYTES:
        raise InvalidObjectKeyError(f"key excede {_MAX_KEY_BYTES} bytes após sanitização: {name!r}")
    return key


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Resultado de um upload bem-sucedido."""

    key: str
    checksum_sha256: str
    size_bytes: int


class ObjectNotFoundError(Exception):
    """Nenhum objeto existe com essa key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"objeto não encontrado: {key}")


class ObjectStoragePort(ABC):
    """Porta de object storage: upload, download e exclusão de objetos
    por key. Todo adapter (`adapters/object_storage/`) implementa isso."""

    @abstractmethod
    async def upload(self, *, key: str, content: bytes, content_type: str) -> StoredObject:
        """Envia `content` para `key`. Retorna a key final (já
        sanitizada) e o checksum SHA-256 calculado sobre os bytes
        enviados — o chamador compara esse checksum com o esperado
        (ex.: `Document.checksum`) para detectar corrupção."""

    @abstractmethod
    async def download(self, *, key: str) -> bytes:
        """Retorna os bytes armazenados em `key`.

        Levanta `ObjectNotFoundError` se `key` não existir."""

    @abstractmethod
    async def delete(self, *, key: str) -> None:
        """Remove o objeto em `key`. Não é erro remover uma key que já
        não existe (exclusão é idempotente)."""
