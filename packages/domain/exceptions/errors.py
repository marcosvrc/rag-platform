"""Exceções de domínio (RAG-010)."""


class DomainError(Exception):
    """Classe base de todas as exceções de domínio."""


class InvalidStatusTransitionError(DomainError):
    """Uma transição de estado não permitida foi solicitada."""

    def __init__(self, *, entity: str, current: str, attempted: str) -> None:
        self.entity = entity
        self.current = current
        self.attempted = attempted
        super().__init__(f"Transição inválida em {entity}: {current} -> {attempted}.")
