"""Provider-neutral LLM interface. The rest of the app depends only on this file."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol


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


class LLMError(Exception):
    """The LLM could not produce a usable answer (network, API error, refusal...)."""


class LLMClient(Protocol):
    """Anything with this method can be used as the app's LLM (Claude, a fake, another provider)."""

    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult: ...
