"""Adapters de `RerankerPort` (RAG-033): `LiteLLMReranker` (reranking real,
via o gateway LiteLLM) e `PassthroughReranker` (usado quando
`Settings.reranker_enabled` é `False` — devolve os candidatos verbatim)."""

from adapters.reranker.litellm import LiteLLMReranker
from adapters.reranker.passthrough import PassthroughReranker

__all__ = ["LiteLLMReranker", "PassthroughReranker"]
