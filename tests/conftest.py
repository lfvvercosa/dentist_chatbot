"""Shared test helpers. pytest loads this file automatically."""

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from app.core.clinic_config import load_clinic_config
from app.core.scheduling_config import load_scheduling_config
from app.llm.client import ChatMessage, LLMClient, LLMError, LLMResult
from app.schemas.appointment import (
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreferenceUpdate,
)
from app.services.availability import AvailabilityService
from app.services.calendar import MockCalendarService
from app.services.escalation import EscalationService


class FakeLLM(LLMClient):
    """Stands in for Claude: records every call and returns "reply 1", "reply 2", ...
    Set `error` to make the next calls fail."""

    def __init__(self):
        self.calls: list[tuple[str, list[ChatMessage]]] = []
        self.error: LLMError | None = None
        self.escalation = "none"
        self.language = "pt"
        self.intent = "other"
        self.service_update: ServicePreferenceUpdate | None = None
        self.dentist_update: DentistPreferenceUpdate | None = None
        self.date_update: DatePreferenceUpdate | None = None
        self.time_update: TimePreferenceUpdate | None = None
        self.booking_answer: str | None = None

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
            intent=self.intent,
            service_update=self.service_update,
            dentist_update=self.dentist_update,
            date_update=self.date_update,
            time_update=self.time_update,
            booking_answer=self.booking_answer,
        )


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def fixed_clock():
    return lambda: datetime(2026, 11, 17, 12, tzinfo=UTC)


@pytest.fixture
def availability_factory():
    def make(clinic):
        return AvailabilityService(
            clinic,
            MockCalendarService({dentist.id: [] for dentist in clinic.dentists}),
            load_scheduling_config(),
        )

    return make


@pytest.fixture
def clinic_config():
    return load_clinic_config()


@pytest.fixture
def escalation_service(clinic_config) -> EscalationService:
    return EscalationService(clinic_config.emergency)
