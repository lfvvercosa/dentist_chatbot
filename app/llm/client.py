"""Provider-neutral LLM interface. The rest of the app depends only on this file."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

EscalationLevel = Literal["none", "urgent", "emergency"]
ResponseLanguage = Literal["pt", "en"]


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    escalation: EscalationLevel = "none"
    language: ResponseLanguage = "pt"


class LLMError(Exception):
    """The LLM could not produce a usable answer (network, API error, refusal...)."""


class LLMClient(ABC):
    """The app's LLM interface. Implementations (Claude, test fakes, another provider) inherit
    from it; a subclass that forgets generate() fails when it is created, not mid-request."""

    @abstractmethod
    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult: ...
