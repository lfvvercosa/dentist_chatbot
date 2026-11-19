"""Saved-offer selection: exact identity, no fresh read, and conversation lifecycle guards."""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_conversation_service
from app.core.scheduling_config import load_scheduling_config
from app.llm.client import ChatMessage, LLMError
from app.main import app
from app.schemas.appointment import (
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

ZONE = ZoneInfo("America/Sao_Paulo")


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
    clock = [datetime(2026, 11, 18, 9, tzinfo=ZONE)]
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


def choose(fake_llm, number=2):
    fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=number)


@pytest.mark.parametrize("language", ["pt", "en"])
def test_choice_uses_saved_exact_option_and_saves_actual_summary(
    offered, fake_llm, language, caplog
):
    service, calendar, _ = offered
    state = service._states["one"]
    before = state.model_copy(deep=True)
    fake_llm.language = language
    choose(fake_llm)
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    reply = service.handle_message("one", "Private patient selection")
    assert state.stage == "slot_selected" and state.selected_slot == before.candidate_slots[1]
    assert state.candidate_slots == before.candidate_slots
    assert state.slots_checked_at == before.slots_checked_at and calendar.reads == 1
    assert "2026-11-19 10:00:00-03:00 → 2026-11-19 10:30:00-03:00" in reply
    assert "Dr. Ana Souza" in reply and "Initial consultation" in reply
    assert "America/Sao_Paulo" in reply
    assert (
        "nada foi reservado ou agendado"
        if language == "pt"
        else "nothing has been reserved or booked"
    ) in reply
    assert (
        "Confirmação e agendamento" if language == "pt" else "Confirmation and booking"
    ) in reply
    assert state.messages[-1] == ChatMessage("assistant", reply)
    assert "stage=slot_selected" in caplog.text and "availability=not_searched" in caplog.text
    assert "Private patient" not in caplog.text and "Dr. Ana" not in caplog.text


def test_reselection_keeps_numbering_and_bare_consent_only_repeats_summary(offered, fake_llm):
    service, calendar, _ = offered
    choose(fake_llm)
    service.handle_message("one", "The second one")
    choose(fake_llm, 3)
    summary = service.handle_message("one", "Actually the third one")
    state = service._states["one"]
    assert state.selected_slot == state.candidate_slots[2]
    assert "10:15:00-03:00" in summary
    fake_llm.slot_update = SlotSelectionUpdate(action="keep", option_number=None)
    assert service.handle_message("one", "Yes, confirm and book it") == summary
    assert state.stage == "slot_selected" and calendar.reads == 1


def test_faq_preserves_selection_and_supplies_it_in_next_prompt(offered, fake_llm):
    service, calendar, _ = offered
    choose(fake_llm)
    summary = service.handle_message("one", "Option 2")
    state = service._states["one"]
    before = state.model_copy(deep=True)
    fake_llm.intent = "other"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    choose(fake_llm, 999)
    assert service.handle_message("one", "What are the opening hours?") == "reply 3"
    assert state.selected_slot == before.selected_slot and state.stage == "slot_selected"
    assert state.candidate_slots == before.candidate_slots and calendar.reads == 1
    context = json.loads(fake_llm.calls[-1][0].rsplit("\n", 1)[1])["current_workflow_state"]
    assert context["selected_slot"] == before.selected_slot.model_dump(mode="json")
    assert fake_llm.calls[-1][1][-2] == ChatMessage("assistant", summary)


@pytest.mark.parametrize("action", ["clear", "out_of_range"])
@pytest.mark.parametrize("language", ["pt", "en"])
def test_ambiguous_or_invalid_choice_clears_selection_and_keeps_offers(
    offered, fake_llm, action, language
):
    service, calendar, _ = offered
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    offers = list(service._states["one"].candidate_slots)
    fake_llm.language = language
    if action == "clear":
        fake_llm.slot_update = SlotSelectionUpdate(action="clear", option_number=None)
    else:
        choose(fake_llm, 999)
    reply = service.handle_message("one", "An unclear choice")
    state = service._states["one"]
    assert state.stage == "offering_slots" and state.selected_slot is None
    assert state.candidate_slots == offers and calendar.reads == 1
    if action == "clear":
        assert reply == "reply 3"
    else:
        assert ("1 a 3" if language == "pt" else "1 to 3") in reply
    choose(fake_llm, 1)
    service.handle_message("one", "Then option 1")
    assert state.selected_slot == offers[0] and calendar.reads == 1


@pytest.mark.parametrize("field", ["service", "dentist", "date", "time", "clear"])
def test_changed_preferences_invalidate_selection_even_with_a_selection_proposal(
    offered, fake_llm, field
):
    service, calendar, _ = offered
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    updates = {
        "service": ("service_update", ServicePreferenceUpdate(action="set", service_id="cleaning")),
        "dentist": ("dentist_update", DentistPreferenceUpdate(action="set", dentist_id="bruno")),
        "date": (
            "date_update",
            DatePreferenceUpdate(
                action="set",
                date_range=DatePreference(start_date="2026-11-20", end_date="2026-11-20"),
            ),
        ),
        "time": (
            "time_update",
            TimePreferenceUpdate(
                action="set",
                preference=TimePreference(kind="afternoon", start_time=None, end_time=None),
            ),
        ),
        "clear": ("time_update", TimePreferenceUpdate(action="clear", preference=None)),
    }
    attribute, update = updates[field]
    setattr(fake_llm, attribute, update)
    service.handle_message("one", "Change my preference and choose option 2")
    state = service._states["one"]
    assert state.selected_slot is None
    assert state.stage == ("collecting_preferences" if field == "clear" else "offering_slots")
    assert calendar.reads == (1 if field == "clear" else 2)


def test_redundant_preference_proposal_does_not_change_the_saved_option_reference(
    offered, fake_llm
):
    service, calendar, _ = offered
    choose(fake_llm)
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )
    service.handle_message("one", "The second morning option")
    assert service._states["one"].selected_slot == service._states["one"].candidate_slots[1]
    assert calendar.reads == 1


@pytest.mark.parametrize("selected_before", [False, True])
def test_elapsed_choice_refreshes_options_without_selecting_from_the_new_list(
    offered, fake_llm, selected_before
):
    service, calendar, clock = offered
    choose(fake_llm)
    if selected_before:
        service.handle_message("one", "Option 2")
        fake_llm.slot_update = SlotSelectionUpdate(action="keep", option_number=None)
    clock[0] = datetime(2026, 11, 19, 10, tzinfo=ZONE)
    service.handle_message("one", "Continue")
    state = service._states["one"]
    assert state.selected_slot is None and state.stage == "offering_slots"
    assert state.candidate_slots[0].interval.start > clock[0]
    assert state.slots_checked_at == clock[0] and calendar.reads == 2


def test_choice_without_a_saved_list_only_creates_fresh_offers(offered, fake_llm):
    service, calendar, _ = offered
    state = service._states["one"]
    state.candidate_slots = []
    state.slots_checked_at = None
    state.stage = "collecting_preferences"
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    assert state.stage == "offering_slots" and state.selected_slot is None
    assert calendar.reads == 2
    service.handle_message("two", "Option 2")
    assert service._states["two"].selected_slot is None
    assert service._states["two"].candidate_slots == [] and calendar.reads == 2


@pytest.mark.parametrize("failure", ["model", "invalid_id"])
def test_failed_turn_preserves_selection_and_history(offered, fake_llm, failure):
    service, calendar, _ = offered
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    before = service._states["one"].model_copy(deep=True)
    choose(fake_llm, 1)
    if failure == "model":
        fake_llm.error = LLMError("test failure")
    else:
        fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
        fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="unknown")
    with pytest.raises(LLMError):
        service.handle_message("one", "Failed correction")
    assert service._states["one"] == before and calendar.reads == 1


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_clears_selection_before_processing_proposals(offered, fake_llm, level):
    service, calendar, _ = offered
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    fake_llm.escalation = level
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    service.handle_message("one", "Urgent concern")
    state = service._states["one"]
    assert state.stage == "chatting" and state.selected_slot is None
    assert state.candidate_slots == [] and state.slots_checked_at is None and calendar.reads == 1


def test_http_contract_keeps_mixed_clinic_answer_before_exact_selection_summary(
    offered, fake_llm, monkeypatch
):
    service, calendar, _ = offered
    monkeypatch.setitem(app.dependency_overrides, get_conversation_service, lambda: service)
    choose(fake_llm)
    fake_llm.booking_answer = "I do not have pricing information."
    response = TestClient(app).post(
        "/chat", json={"conversation_id": "one", "message": "Option 2? Price?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"conversation_id", "reply"}
    assert body["reply"].startswith(
        fake_llm.booking_answer + "\n\nOpção de demonstração selecionada"
    )
    assert service._states["one"].messages[-1].content == body["reply"]
    assert service._states["one"].selected_slot is not None and calendar.reads == 1


def test_selecting_a_dentist_from_any_options_preserves_the_any_preference(offered, fake_llm):
    service, calendar, _ = offered
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    service.handle_message("one", "Any eligible dentist")
    state = service._states["one"]
    expected = state.candidate_slots[1]
    assert expected.dentist_id == "bruno"
    reads = calendar.reads
    fake_llm.dentist_update = None
    choose(fake_llm)
    service.handle_message("one", "The second option")
    assert state.selected_slot == expected and state.dentist_preference.kind == "any"
    assert calendar.reads == reads


@pytest.mark.parametrize("expiry", ["date", "time"])
def test_expired_preferences_clear_selection_before_any_calendar_read(offered, fake_llm, expiry):
    service, calendar, clock = offered
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    clock[0] = datetime(2026, 11, 20 if expiry == "date" else 19, 13, tzinfo=ZONE)
    service.handle_message("one", "Continue")
    state = service._states["one"]
    assert state.selected_slot is None and state.stage == "collecting_preferences"
    assert state.candidate_slots == [] and state.slots_checked_at is None and calendar.reads == 1


def test_failed_search_after_correction_clears_selection_and_retry_only_offers(offered, fake_llm):
    service, calendar, _ = offered
    choose(fake_llm)
    service.handle_message("one", "Option 2")
    calendar.fail = True
    fake_llm.slot_update = None
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="afternoon", start_time=None, end_time=None)
    )
    reply = service.handle_message("one", "Afternoon instead")
    state = service._states["one"]
    assert "Não foi possível consultar" in reply
    assert state.selected_slot is None and state.candidate_slots == []
    assert state.time_preference.kind == "afternoon"
    calendar.fail = False
    fake_llm.time_update = None
    service.handle_message("one", "Try again")
    assert state.stage == "offering_slots" and state.selected_slot is None
