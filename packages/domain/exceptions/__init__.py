"""Exceções de domínio que representam violações de invariantes de negócio."""

from packages.domain.exceptions.errors import DomainError, InvalidStatusTransitionError

__all__ = ["DomainError", "InvalidStatusTransitionError"]
