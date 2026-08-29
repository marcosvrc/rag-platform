"""Avaliação de feedback (seção do plano sobre métricas de produto:
"feedback positivo e negativo")."""

from enum import StrEnum


class FeedbackRating(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
