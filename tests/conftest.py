"""Shared test helpers. pytest loads this file automatically."""

from collections.abc import Sequence

import pytest

from app.core.clinic_config import load_clinic_config
from app.llm.client import ChatMessage, LLMClient, LLMError, LLMResult
from app.services.escalation import EscalationService


class FakeLLM(LLMClient):
    """Stands in for Claude: records every call and returns "reply 1", "reply 2", ...
    Set `error` to make the next calls fail."""

    def __init__(self):
        self.calls: list[tuple[str, list[ChatMessage]]] = []
        self.error: LLMError | None = None
        self.escalation = "none"
        self.language = "pt"

    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult:
        self.calls.append((system, list(messages)))
        if self.error:
            raise self.error
        return LLMResult(
            text=f"reply {len(self.calls)}",
            model="fake-model",
            input_tokens=10,
            output_tokens=5,
            escalation=self.escalation,
            language=self.language,
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def escalation_service() -> EscalationService:
    return EscalationService(load_clinic_config().emergency)
