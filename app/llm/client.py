"""Provider-neutral LLM interface. The rest of the app depends only on this file."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.schemas.appointment import (
    ConfirmationUpdate,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    SlotSelectionUpdate,
    TimePreferenceUpdate,
)

EscalationLevel = Literal["none", "urgent", "emergency"]
ResponseLanguage = Literal["pt", "en"]
ConversationIntent = Literal["other", "booking"]


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
    intent: ConversationIntent = "other"
    service_update: ServicePreferenceUpdate | None = None
    dentist_update: DentistPreferenceUpdate | None = None
    date_update: DatePreferenceUpdate | None = None
    time_update: TimePreferenceUpdate | None = None
    slot_update: SlotSelectionUpdate | None = None
    confirmation_update: ConfirmationUpdate | None = None
    booking_answer: str | None = None


class LLMError(Exception):
    """The LLM could not produce a usable answer (network, API error, refusal...)."""


class LLMClient(ABC):
    """The app's LLM interface. Implementations (Claude, test fakes, another provider) inherit
    from it; a subclass that forgets generate() fails when it is created, not mid-request."""

    @abstractmethod
    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult: ...
