"""Porta de fila de jobs de indexação (RAG-022).

Casos de uso dependem só desta porta, nunca de Celery diretamente
(seção 5.1 do plano) — o adapter concreto (`adapters/queue/`) é quem
sabe que existe um broker Redis por trás.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


class JobQueuePort(ABC):
    """Publica um `IndexJob` já persistido (seção 11, passo 6 do plano)
    para processamento assíncrono por um worker."""

    @abstractmethod
    def enqueue_index_job(self, *, index_job_id: UUID) -> None:
        """Publica `index_job_id` na fila.

        Só o `id` é publicado, nunca o payload completo do job: o
        worker sempre relê o estado atual do banco antes de processar
        (evita agir sobre dados potencialmente obsoletos se o job for
        atualizado entre a publicação e o consumo). Não lança em caso
        de sucesso da publicação em si — falhas de conectividade com o
        broker propagam como a exceção nativa do adapter (não há uma
        exceção de aplicação dedicada para isso ainda; nenhuma
        atividade até aqui precisou distinguir esse caso)."""
