"""Porta do pipeline de processamento de um job de indexação (RAG-022).

Define o ponto de extensão entre a infraestrutura de fila/retry/falha
definitiva que esta atividade entrega (RAG-022, seção 11, passos 6-7 do
plano: publicar o job e o worker adquirir um lock idempotente) e o
pipeline de indexação em si (extração, normalização, chunking,
embeddings e persistência — RAG-023 a RAG-027, passos 8-14), que ainda
não existe. Sem esta porta, o worker do RAG-022 não teria nada
concreto para chamar nem como testar retry/falha definitiva de forma
determinística (ver `adapters/document_processor/in_memory.py`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


class DocumentProcessorPort(ABC):
    """Processa um `IndexJob` já reivindicado (RUNNING) até o fim.

    Implementações devem levantar qualquer exceção para sinalizar
    falha — quem chama (`packages.application.commands.index_job`)
    decide se ainda há tentativas restantes (retry exponencial) ou se
    é uma falha definitiva; esta porta não deve fazer sua própria
    lógica de retry."""

    @abstractmethod
    async def process(self, *, index_job_id: UUID) -> None:
        """Executa o pipeline de indexação para `index_job_id`."""
