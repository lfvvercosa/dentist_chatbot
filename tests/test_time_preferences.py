"""Time preferences use fixed clocks and scripted proposals, never a live model/calendar."""

import json
from datetime import UTC, date, datetime, time

import pytest
from pydantic import ValidationError

from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import (
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.services.conversation import ConversationService


@pytest.fixture
def service(fake_llm, escalation_service, clinic_config, fixed_clock):
    fake_llm.intent = "booking"
    return ConversationService(
        fake_llm, "base prompt", escalation_service, clinic_config, clock=fixed_clock
    )


def choose_time(fake_llm, kind="window", start=None, end=None):
    preference = TimePreference(kind=kind, start_time=start, end_time=end)
    fake_llm.time_update = TimePreferenceUpdate(action="set", preference=preference)
    return preference


def choose_dates(fake_llm, start="2026-11-17", end=None):
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date=start, end_date=end or start)
    )


def sent_context(fake_llm):
    return json.loads(fake_llm.calls[-1][0].rsplit("\n", 1)[1])


def test_period_exact_window_correction_keep_and_saved_context(service, fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    choose_dates(fake_llm, "2026-11-18")
    morning = choose_time(fake_llm, "morning")
    service.handle_message("one", "Limpeza com Ana amanhã de manhã")
    state = service._states["one"]
    assert state.time_preference == morning
    assert sent_context(fake_llm)["current_workflow_state"]["time_preference"] is None

    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    exact = choose_time(fake_llm, start="14:00", end="14:00")
    service.handle_message("one", "Actually, at 2pm")
    assert state.time_preference == exact
    assert exact.start_time == exact.end_time == time(14)
    assert sent_context(fake_llm)["current_workflow_state"]["time_preference"] == {
        "kind": "morning",
        "start_time": None,
        "end_time": None,
    }

    window = choose_time(fake_llm, start="14:00", end="16:00")
    service.handle_message("one", "Entre 14 e 16")
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)
    service.handle_message("one", "Which details are missing?")
    assert state.time_preference == window
    assert state.service_id == "cleaning" and state.dentist_preference.dentist_id == "ana"
    assert state.date_preference.start_date == date(2026, 11, 18)
    assert sent_context(fake_llm)["current_workflow_state"]["time_preference"] == {
        "kind": "window",
        "start_time": "14:00:00",
        "end_time": "16:00:00",
    }


@pytest.mark.parametrize("message", ["Vou decidir o horário depois", "Actually, at 7"])
def test_explicit_any_differs_from_unanswered_and_clear_removes_superseded_time(
    service, fake_llm, message
):
    assert service.handle_message("one", "Book") == "Qual serviço você deseja agendar?"
    assert service._states["one"].time_preference is None
    any_time = choose_time(fake_llm, "any")
    service.handle_message("one", "Qualquer horário")
    assert service._states["one"].time_preference == any_time
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    service.handle_message("one", message)
    assert service._states["one"].time_preference is None
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)
    service.handle_message("one", "Continue")
    assert sent_context(fake_llm)["current_workflow_state"]["time_preference"] is None


@pytest.mark.parametrize("language", ["pt", "en"])
@pytest.mark.parametrize("start,end", [("08:00", "08:00"), ("07:00", "08:59"), (None, "08:59")])
def test_elapsed_today_time_clears_only_time_and_saves_backend_clarification(
    service, fake_llm, language, start, end
):
    fake_llm.language = language
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    choose_dates(fake_llm)
    choose_time(fake_llm, start=start, end=end)
    reply = service.handle_message("one", "My time")
    state = service._states["one"]
    assert state.time_preference is None
    assert state.service_id == "cleaning" and state.dentist_preference.dentist_id == "ana"
    assert state.date_preference.start_date == date(2026, 11, 17)
    assert ("já passou hoje" if language == "pt" else "already passed today") in reply
    assert state.messages[-1] == ChatMessage("assistant", reply)


@pytest.mark.parametrize(
    "kind,start,end",
    [
        ("window", "09:00", "09:00"),  # Inclusive exact boundary at the fixed 09:00 clock.
        ("window", "08:00", "10:00"),  # Partially elapsed; preserve the full window.
        ("window", "08:00", None),
        ("window", None, "10:00"),
        ("window", "23:00", "23:00"),  # Opening hours are a later check.
        ("morning", None, None),
        ("afternoon", None, None),
        ("evening", None, None),
        ("any", None, None),
    ],
)
def test_today_preserves_preferences_that_have_not_fully_elapsed(
    service, fake_llm, kind, start, end
):
    choose_dates(fake_llm)
    preference = choose_time(fake_llm, kind, start, end)
    assert service.handle_message("one", "My time") == "Qual serviço você deseja agendar?"
    assert service._states["one"].time_preference == preference


@pytest.mark.parametrize("dates", [None, ("2026-11-18", None), ("2026-11-17", "2026-11-18")])
def test_time_is_kept_without_date_or_with_future_days(service, fake_llm, dates):
    if dates is not None:
        choose_dates(fake_llm, *dates)
    preference = choose_time(fake_llm, start="08:00", end="08:00")
    service.handle_message("one", "At 8am")
    assert service._states["one"].time_preference == preference


@pytest.mark.parametrize(
    "kind,instant,expired",
    [
        ("morning", datetime(2026, 11, 17, 14, 59, 59, tzinfo=UTC), False),
        ("morning", datetime(2026, 11, 17, 15, tzinfo=UTC), True),
        ("afternoon", datetime(2026, 11, 17, 20, 59, 59, tzinfo=UTC), False),
        ("afternoon", datetime(2026, 11, 17, 21, tzinfo=UTC), True),
        ("evening", datetime(2026, 11, 18, 2, 59, 59, tzinfo=UTC), False),
    ],
)
def test_named_period_boundaries_use_clinic_local_time(
    fake_llm, escalation_service, clinic_config, kind, instant, expired
):
    fake_llm.intent = "booking"
    service = ConversationService(
        fake_llm, "base", escalation_service, clinic_config, clock=lambda: instant
    )
    choose_dates(fake_llm)
    preference = choose_time(fake_llm, kind)
    service.handle_message("one", "My period")
    assert service._states["one"].time_preference == (None if expired else preference)
    assert sent_context(fake_llm)["time_periods"] == {
        "morning": {"start_time": "00:00:00", "end_time_exclusive": "12:00:00"},
        "afternoon": {"start_time": "12:00:00", "end_time_exclusive": "18:00:00"},
        "evening": {"start_time": "18:00:00", "end_time_exclusive": None},
    }


def test_date_correction_rechecks_retained_time_without_replacing_other_preferences(
    service, fake_llm
):
    choose_dates(fake_llm, "2026-11-18")
    choose_time(fake_llm, start="08:00", end="08:00")
    service.handle_message("one", "Tomorrow at 8am")
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)
    choose_dates(fake_llm)
    reply = service.handle_message("one", "Actually, today")
    assert service._states["one"].time_preference is None
    assert "já passou hoje" in reply


def test_retained_time_expires_on_booking_turn_after_clock_advances(
    fake_llm, escalation_service, clinic_config
):
    instant = [datetime(2026, 11, 17, 12, tzinfo=UTC)]
    service = ConversationService(
        fake_llm, "base", escalation_service, clinic_config, clock=lambda: instant[0]
    )
    fake_llm.intent = "booking"
    choose_dates(fake_llm)
    choose_time(fake_llm, start="10:00", end="10:00")
    service.handle_message("one", "Today at 10am")
    instant[0] = datetime(2026, 11, 17, 13, 0, 1, tzinfo=UTC)
    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)
    assert "já passou hoje" in service.handle_message("one", "Continue")
    assert service._states["one"].time_preference is None


def test_faq_preserves_time_and_conversations_do_not_share_it(service, fake_llm, caplog):
    caplog.set_level("INFO", logger="app.services.conversation")
    preference = choose_time(fake_llm, start="14:07", end="16:03")
    service.handle_message("one", "My private time 14:07 to 16:03")
    fake_llm.intent = "other"
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    service.handle_message("one", "What are your hours?")
    service.handle_message("two", "Hello")
    assert service._states["one"].time_preference == preference
    assert service._states["two"].time_preference is None
    assert sent_context(fake_llm)["current_workflow_state"]["time_preference"] is None
    assert "14:07" not in caplog.text and "16:03" not in caplog.text


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_clears_all_preferences_and_overrides_elapsed_time(
    service, fake_llm, escalation_service, level
):
    choose_dates(fake_llm)
    choose_time(fake_llm, start="10:00", end="10:00")
    service.handle_message("one", "Today at 10")
    choose_time(fake_llm, start="08:00", end="08:00")
    fake_llm.escalation = level
    assert service.handle_message("one", "Help") == escalation_service.reply(level, "pt")
    state = service._states["one"]
    assert state.time_preference is None and state.date_preference is None
    assert state.service_id is None and state.dentist_preference.kind == "unspecified"
    assert state.stage == "chatting"


@pytest.mark.parametrize("failure", ["provider", "service_id", "dentist_id"])
def test_failed_turn_preserves_time_and_history_and_next_prompt(service, fake_llm, failure):
    preference = choose_time(fake_llm, "afternoon")
    service.handle_message("one", "À tarde")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    if failure == "provider":
        fake_llm.error = LLMError("unavailable")
    elif failure == "service_id":
        fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    else:
        fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="unknown")
    with pytest.raises(LLMError):
        service.handle_message("one", "Failed correction")
    assert service._states["one"] == before
    fake_llm.error = None
    fake_llm.service_update = None
    fake_llm.dentist_update = None
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)
    service.handle_message("one", "Continue")
    assert sent_context(fake_llm)["current_workflow_state"][
        "time_preference"
    ] == preference.model_dump(mode="json")
    assert ChatMessage("user", "Failed correction") not in fake_llm.calls[-1][1]


def test_past_date_takes_priority_and_time_is_preserved_for_a_replacement_date(service, fake_llm):
    choose_dates(fake_llm, "2026-11-16")
    preference = choose_time(fake_llm, start="08:00", end="08:00")
    reply = service.handle_message("one", "Yesterday at 8")
    assert "data" in reply and "já passou" in reply
    assert service._states["one"].date_preference is None
    assert service._states["one"].time_preference == preference


def test_elapsed_time_takes_priority_while_dentist_conflict_remains(service, fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    choose_dates(fake_llm)
    choose_time(fake_llm, start="08:00", end="08:00")
    assert "já passou hoje" in service.handle_message("one", "My preferences")
    state = service._states["one"]
    assert state.service_id == "cleaning" and state.dentist_preference.dentist_id == "bruno"


@pytest.mark.parametrize("value", [datetime(2026, 11, 17, 14), time(14, tzinfo=UTC), 3600])
def test_python_time_inputs_also_reject_dates_offsets_and_numbers(value):
    with pytest.raises(ValidationError):
        TimePreference(kind="window", start_time=value, end_time=None)
