"""Mock-backed chat offers: actual reads, stored candidates, reply priority, and HTTP wiring."""

import importlib
import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_conversation_service
from app.core.clinic_config import TimeRange, Weekday
from app.core.scheduling_config import load_scheduling_config
from app.core.settings import Settings
from app.llm.client import ChatMessage, LLMError
from app.main import app
from app.schemas.appointment import (
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.schemas.calendar import TimeInterval
from app.services.availability import AvailabilityService
from app.services.calendar import CalendarError, MockCalendarService
from app.services.conversation import ConversationService

ZONE = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 11, 18, 9, tzinfo=ZONE)


class RecordingCalendar(MockCalendarService):
    def __init__(self, seeds):
        super().__init__(seeds)
        self.calls = []
        self.fail = False

    def get_busy_intervals(self, dentist_id, window):
        self.calls.append((dentist_id, window))
        if self.fail:
            raise CalendarError("private provider detail")
        return super().get_busy_intervals(dentist_id, window)


def fill(fake_llm, *, day="2026-11-19", dentist="ana"):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(
        action="any" if dentist == "any" else "set",
        dentist_id=None if dentist == "any" else dentist,
    )
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date=day, end_date=day)
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )


def keep(fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)


@pytest.fixture
def make_service(fake_llm, escalation_service, clinic_config):
    def make(*, periods=(), clinic=None, now=NOW):
        clinic = clinic or clinic_config
        seeds = {dentist.id: [] for dentist in clinic.dentists}
        seeds["ana"] = periods
        calendar = RecordingCalendar(seeds)
        clock = [now]
        service = ConversationService(
            fake_llm,
            "base prompt",
            escalation_service,
            clinic,
            availability=AvailabilityService(clinic, calendar, load_scheduling_config()),
            clock=lambda: clock[0],
        )
        return service, calendar, clock

    return make


@pytest.mark.parametrize("language", ["pt", "en"])
def test_complete_preferences_return_exact_read_backed_candidates_and_save_actual_reply(
    make_service, fake_llm, language, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    blocked = TimeInterval(start="2026-11-19T08:30:00-03:00", end="2026-11-19T10:00:00-03:00")
    service, calendar, _ = make_service(periods=[blocked])
    fill(fake_llm)
    fake_llm.language = language
    reply = service.handle_message("one", "Private patient request")
    state = service._states["one"]
    assert state.stage == "offering_slots"
    assert len(state.candidate_slots) == 3 and len(calendar.calls) == 1
    assert [
        slot.interval.start.astimezone(ZONE).strftime("%H:%M") for slot in state.candidate_slots
    ] == ["08:00", "10:00", "10:15"]
    assert all(not slot.interval.overlaps(blocked) for slot in state.candidate_slots)
    assert state.slots_checked_at == NOW.astimezone(UTC)
    assert state.messages[-1] == ChatMessage("assistant", reply)
    assert "America/Sao_Paulo" in reply and "Dr. Ana Souza" in reply
    assert "2026-11-19 10:00:00-03:00" in reply
    assert (
        "nada foi reservado ou agendado"
        if language == "pt"
        else "nothing has been reserved or booked"
    ) in reply
    assert "reply 1" not in reply
    assert "availability=found offered_slots=3" in caplog.text
    assert "Private patient request" not in caplog.text and "Dr. Ana Souza" not in caplog.text


def test_incomplete_and_conflicting_preferences_skip_reads_and_clear_old_offers(
    make_service, fake_llm
):
    service, calendar, _ = make_service()
    fill(fake_llm)
    service.handle_message("one", "Complete")
    keep(fake_llm)
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    assert service.handle_message("one", "Decide time later") == "reply 2"
    state = service._states["one"]
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.stage == "collecting_preferences" and len(calendar.calls) == 1
    fill(fake_llm)
    service.handle_message("one", "Complete again")
    keep(fake_llm)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="carla")
    assert "não oferece" in service.handle_message("one", "With Carla")
    assert len(calendar.calls) == 2
    assert state.candidate_slots == [] and state.slots_checked_at is None


def test_date_correction_replaces_offers_with_newly_checked_dates(make_service, fake_llm):
    service, calendar, _ = make_service()
    fill(fake_llm)
    service.handle_message("one", "Tomorrow")
    before = service._states["one"].candidate_slots.copy()
    keep(fake_llm)
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-20", end_date="2026-11-20")
    )
    reply = service.handle_message("one", "Actually Friday")
    after = service._states["one"].candidate_slots
    assert after != before and len(calendar.calls) == 2
    assert all(
        slot.interval.start.astimezone(ZONE).date().isoformat() == "2026-11-20" for slot in after
    )
    assert "2026-11-19" not in reply and "2026-11-20" in reply


@pytest.mark.parametrize("language", ["pt", "en"])
def test_horizon_error_reports_actual_limits_keeps_choices_and_clears_stale_offers(
    make_service, fake_llm, language
):
    service, calendar, _ = make_service()
    fill(fake_llm)
    service.handle_message("one", "Complete")
    keep(fake_llm)
    fake_llm.language = language
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2027-01-01", end_date="2027-01-01")
    )
    reply = service.handle_message("one", "Next year")
    state = service._states["one"]
    assert "2026-11-18" in reply and "2026-12-17" in reply
    assert state.date_preference.start_date.isoformat() == "2027-01-01"
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.stage == "collecting_preferences" and len(calendar.calls) == 1
    assert state.messages[-1].content == reply


@pytest.mark.parametrize("language", ["pt", "en"])
def test_no_matching_slots_is_a_normal_distinct_reply(make_service, fake_llm, language, caplog):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    blocked = TimeInterval(start="2026-11-19T00:00:00-03:00", end="2026-11-20T00:00:00-03:00")
    service, calendar, _ = make_service(periods=[blocked])
    fill(fake_llm)
    fake_llm.language = language
    reply = service.handle_message("one", "Complete")
    assert ("Não encontrei horários" if language == "pt" else "I found no matching slots") in reply
    state = service._states["one"]
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.stage == "collecting_preferences" and len(calendar.calls) == 1
    assert "availability=none_found offered_slots=0" in caplog.text


@pytest.mark.parametrize("language", ["pt", "en"])
def test_read_failure_clears_stale_offers_preserves_draft_and_can_recover(
    make_service, fake_llm, language, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    service, calendar, _ = make_service()
    fill(fake_llm)
    first = service.handle_message("one", "Complete")
    keep(fake_llm)
    fake_llm.language = language
    calendar.fail = True
    reply = service.handle_message("one", "Try again")
    state = service._states["one"]
    assert ("Não foi possível consultar" if language == "pt" else "could not be read") in reply
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.service_id == "consultation" and state.date_preference is not None
    assert state.stage == "collecting_preferences"
    assert state.messages[-1].content == reply
    assert "availability=read_error offered_slots=0" in caplog.text
    assert "private provider detail" not in caplog.text + reply
    calendar.fail = False
    assert service.handle_message("one", "Try again") != reply
    assert service._states["one"].candidate_slots and len(calendar.calls) == 3
    assert first in [message.content for message in state.messages]


@pytest.mark.parametrize("failure", ["provider", "service_id", "dentist_id"])
def test_failed_interpretation_preserves_accepted_offers_and_history(
    make_service, fake_llm, failure
):
    service, calendar, _ = make_service()
    fill(fake_llm)
    service.handle_message("one", "Complete")
    before = service._states["one"].model_copy(deep=True)
    if failure == "provider":
        fake_llm.error = LLMError("unavailable")
    elif failure == "service_id":
        fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    else:
        fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="unknown")
    with pytest.raises(LLMError):
        service.handle_message("one", "Rejected correction")
    assert service._states["one"] == before
    assert len(calendar.calls) == 1


def test_faq_preserves_offer_snapshot_without_a_fresh_read(make_service, fake_llm):
    service, calendar, clock = make_service()
    fill(fake_llm)
    first = service.handle_message("one", "Complete")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.intent = "other"
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    clock[0] += timedelta(hours=1)
    assert service.handle_message("one", "What is your address?") == "reply 2"
    state = service._states["one"]
    assert state.candidate_slots == before.candidate_slots
    assert state.slots_checked_at == before.slots_checked_at and state.stage == "offering_slots"
    assert len(calendar.calls) == 1
    context = json.loads(fake_llm.calls[-1][0].rsplit("\n", 1)[1])["current_workflow_state"]
    assert context["candidate_slots"] == [
        slot.model_dump(mode="json") for slot in before.candidate_slots
    ]
    assert datetime.fromisoformat(context["slots_checked_at"]) == before.slots_checked_at
    assert fake_llm.calls[-1][1][-2] == ChatMessage("assistant", first)


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_clears_offers_and_skips_calendar_even_with_invalid_proposals(
    make_service, fake_llm, escalation_service, level
):
    service, calendar, _ = make_service()
    fill(fake_llm)
    service.handle_message("one", "Complete")
    fake_llm.escalation = level
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    assert service.handle_message("one", "Help") == escalation_service.reply(level, "pt")
    state = service._states["one"]
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.stage == "chatting" and len(calendar.calls) == 1


@pytest.mark.parametrize("expired", ["date", "time"])
def test_expired_preferences_clear_old_offers_before_any_new_read(make_service, fake_llm, expired):
    service, calendar, clock = make_service()
    fill(fake_llm)
    if expired == "time":
        fake_llm.time_update = TimePreferenceUpdate(
            action="set",
            preference=TimePreference(kind="window", start_time="08:00", end_time="08:00"),
        )
    service.handle_message("one", "Complete")
    keep(fake_llm)
    clock[0] = datetime(2026, 11, 20 if expired == "date" else 19, 9, tzinfo=ZONE)
    assert "já passou" in service.handle_message("one", "Continue")
    state = service._states["one"]
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.stage == "collecting_preferences" and len(calendar.calls) == 1


def test_numbered_choice_or_confirmation_only_refreshes_options_in_this_slice(
    make_service, fake_llm
):
    service, calendar, _ = make_service()
    fill(fake_llm)
    first = service.handle_message("one", "Complete")
    keep(fake_llm)
    assert service.handle_message("one", "Yes, book option 1") == first
    assert service._states["one"].stage == "offering_slots"
    assert len(calendar.calls) == 2
    assert "A escolha de opções ainda não está disponível" in first


def test_other_conversation_does_not_inherit_offers(make_service, fake_llm):
    service, calendar, _ = make_service()
    fill(fake_llm)
    service.handle_message("one", "Complete")
    keep(fake_llm)
    service.handle_message("two", "Book")
    assert service._states["two"].candidate_slots == []
    assert service._states["two"].stage == "collecting_preferences" and len(calendar.calls) == 1


def test_mixed_clinic_answer_is_kept_before_checked_offers(make_service, fake_llm):
    service, _, _ = make_service()
    fill(fake_llm)
    fake_llm.booking_answer = "I do not have pricing information."
    reply = service.handle_message("one", "Book this, and what is the price?")
    assert reply.startswith(fake_llm.booking_answer + "\n\nOpções verificadas")


def test_repeated_local_time_options_include_both_explicit_offsets(
    make_service, fake_llm, clinic_config
):
    zone = ZoneInfo("America/New_York")
    clinic = clinic_config.model_copy(
        update={
            "timezone": str(zone),
            "opening_hours": {Weekday.SUNDAY: TimeRange(open="00:00", close="04:00")},
        }
    )
    service, _, _ = make_service(clinic=clinic, now=datetime(2026, 10, 31, 9, tzinfo=zone))
    fill(fake_llm, day="2026-11-01")
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="window", start_time="01:30", end_time="01:30")
    )
    reply = service.handle_message("one", "At 1:30am")
    assert "2026-11-01 01:30:00-04:00" in reply and "2026-11-01 01:30:00-05:00" in reply
    assert len(service._states["one"].candidate_slots) == 2


def test_unsupported_clinic_boundary_gets_generic_search_error(
    make_service, fake_llm, clinic_config
):
    zone = ZoneInfo("America/New_York")
    clinic = clinic_config.model_copy(
        update={
            "timezone": str(zone),
            "opening_hours": {Weekday.SUNDAY: TimeRange(open="02:30", close="04:00")},
        }
    )
    service, calendar, _ = make_service(clinic=clinic, now=datetime(2026, 3, 7, 9, tzinfo=zone))
    fill(fake_llm, day="2026-03-08")
    reply = service.handle_message("one", "Complete")
    assert "Não consegui pesquisar horários" in reply
    assert service._states["one"].candidate_slots == [] and calendar.calls == []


@pytest.mark.parametrize("outcome", ["found", "empty", "invalid_search", "read_error"])
def test_chat_http_contract_for_availability_outcomes(make_service, fake_llm, monkeypatch, outcome):
    periods = (
        [TimeInterval(start="2026-11-19T00:00:00-03:00", end="2026-11-20T00:00:00-03:00")]
        if outcome == "empty"
        else []
    )
    service, calendar, _ = make_service(periods=periods)
    fill(fake_llm, day="2027-01-01" if outcome == "invalid_search" else "2026-11-19")
    calendar.fail = outcome == "read_error"
    monkeypatch.setitem(app.dependency_overrides, get_conversation_service, lambda: service)
    response = TestClient(app).post("/chat", json={"conversation_id": "one", "message": "Complete"})
    assert response.status_code == 200
    assert set(response.json()) == {"conversation_id", "reply"}
    assert response.json()["reply"] == service._states["one"].messages[-1].content
    assert bool(service._states["one"].candidate_slots) == (outcome == "found")


def test_real_api_startup_wires_mock_availability_without_credentials(fake_llm, monkeypatch):
    main_module = importlib.import_module("app.main")
    monkeypatch.setattr(main_module, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(main_module, "build_claude_client", lambda settings: fake_llm)
    with TestClient(app) as client:
        app.state.conversation_service._clock = lambda: NOW
        fill(fake_llm)
        response = client.post("/chat", json={"conversation_id": "startup", "message": "Complete"})
        assert response.status_code == 200
        assert "Opções verificadas" in response.json()["reply"]
        assert isinstance(app.state.calendar_service, MockCalendarService)
        assert app.state.conversation_service._states["startup"].stage == "offering_slots"
