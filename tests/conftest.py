"""Shared test helpers. pytest loads this file automatically."""

from collections.abc import Sequence

import pytest

from app.llm.client import ChatMessage, LLMError, LLMResult


class FakeLLM:
    """Stands in for Claude: records every call and returns "reply 1", "reply 2", ...
    Set `error` to make the next calls fail."""

    def __init__(self):
        self.calls: list[tuple[str, list[ChatMessage]]] = []
        self.error: LLMError | None = None

    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult:
        self.calls.append((system, list(messages)))
        if self.error:
            raise self.error
        return LLMResult(
            text=f"reply {len(self.calls)}", model="fake-model", input_tokens=10, output_tokens=5
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
