"""Smoke tests for RAG-001: garantem que a estrutura de pacotes é importável.

Cobre o critério de aceite "imports funcionam" sem depender de nenhuma
infraestrutura externa (banco, fila, object storage, LLM).
"""

import importlib

PACKAGES = [
    "apps",
    "apps.api",
    "apps.api.routers",
    "apps.indexing_worker",
    "apps.evaluation_worker",
    "packages.domain",
    "packages.domain.entities",
    "packages.domain.enums",
    "packages.domain.exceptions",
    "packages.domain.services",
    "packages.application",
    "packages.application.commands",
    "packages.application.queries",
    "packages.application.ports",
    "packages.application.use_cases",
    "packages.contracts",
    "packages.ingestion",
    "packages.retrieval",
    "packages.generation",
    "packages.observability",
    "adapters",
    "adapters.postgres",
    "adapters.object_storage",
    "adapters.queue",
    "adapters.docling",
    "adapters.litellm",
    "adapters.evaluation",
]


def test_all_scaffolded_packages_are_importable():
    for module_name in PACKAGES:
        importlib.import_module(module_name)
