"""LLM backend selection.

Real usage talks to a local Ollama daemon via `langchain-ollama`. The test
suite (and CI) swap in LangChain's own `FakeListChatModel` -- a standard,
first-party testing utility, not a hand-rolled mock -- so the graph's
routing and remediation-loop logic can be exercised deterministically
without a GPU or a downloaded model.
"""

from __future__ import annotations

import os

from langchain_core.language_models.chat_models import BaseChatModel


def get_llm() -> BaseChatModel:
    backend = os.environ.get("AGENT_LLM_BACKEND", "ollama")

    if backend == "fake":
        import json as _json

        from langchain_core.language_models.fake_chat_models import FakeListChatModel

        raw = os.environ.get("AGENT_FAKE_LLM_RESPONSES", "[]")
        responses = _json.loads(raw)
        return FakeListChatModel(responses=responses)

    if backend == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=os.environ.get("OLLAMA_MODEL", "llama3.1"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0,
        )

    raise ValueError(f"Unknown AGENT_LLM_BACKEND: {backend!r}")
