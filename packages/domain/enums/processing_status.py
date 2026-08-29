"""Estado genérico de processamento assíncrono.

Reaproveitado por IndexJob e EvaluationRun (RAG-010): a seção 9 do
plano não detalha os valores para esses dois, e ambos seguem o mesmo
formato de job com retry descrito nas seções sobre indexação
("retry exponencial funciona; falha definitiva é registrada").
"""

from enum import StrEnum


class ProcessingStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
