"""Shared test helpers. pytest loads this file automatically."""

from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.clinic_config import load_clinic_config
from app.core.scheduling_config import load_scheduling_config
from app.llm.client import ChatMessage, LLMClient, LLMError, LLMResult
from app.schemas.appointment import (
    ConfirmationUpdate,
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    SlotSelectionUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.schemas.calendar import TimeInterval
from app.services.availability import AvailabilityService
from app.services.calendar import CalendarError, MockCalendarService
from app.services.conversation import ConversationService
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
        self.slot_update: SlotSelectionUpdate | None = None
        self.confirmation_update: ConfirmationUpdate | None = None
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
            slot_update=self.slot_update,
            confirmation_update=self.confirmation_update,
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


class CountingCalendar(MockCalendarService):
    def __init__(self, clinic):
        super().__init__(
            {dentist.id: [] for dentist in clinic.dentists}
            | {
                "ana": [
                    TimeInterval(start="2026-11-19T08:30:00-03:00", end="2026-11-19T10:00:00-03:00")
                ]
            }
        )
        self.reads = 0
        self.fail = False

    def get_busy_intervals(self, dentist_id, window):
        self.reads += 1
        if self.fail:
            raise CalendarError("test read failure")
        return super().get_busy_intervals(dentist_id, window)


@pytest.fixture
def offered(fake_llm, clinic_config, escalation_service):
    calendar = CountingCalendar(clinic_config)
    clock = [datetime(2026, 11, 18, 9, tzinfo=ZoneInfo(clinic_config.timezone))]
    service = ConversationService(
        fake_llm,
        "base prompt",
        escalation_service,
        clinic_config,
        availability=AvailabilityService(clinic_config, calendar, load_scheduling_config()),
        clock=lambda: clock[0],
    )
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-19", end_date="2026-11-19")
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )
    service.handle_message("one", "A consultation with Ana tomorrow morning")
    for field in ("service_update", "dentist_update", "date_update", "time_update"):
        setattr(fake_llm, field, None)
    return service, calendar, clock


@pytest.fixture
def pending(offered, fake_llm):
    service, calendar, clock = offered
    fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=2)
    service.handle_message("one", "Option 2")
    fake_llm.slot_update = None
    return service, calendar, clock
