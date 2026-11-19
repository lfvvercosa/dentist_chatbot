"""Calendar-date preferences use fixed clocks and scripted LLM proposals, never the network."""

import json
import logging
from datetime import UTC, date, datetime

import pytest

from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import (
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
)
from app.services.conversation import ConversationService


@pytest.fixture
def service(fake_llm, escalation_service, clinic_config, fixed_clock, availability_factory):
    fake_llm.intent = "booking"
    return ConversationService(
        fake_llm,
        "base prompt",
        escalation_service,
        clinic_config,
        clock=fixed_clock,
        availability=availability_factory(clinic_config),
    )


def choose_dates(fake_llm, start, end=None):
    preference = DatePreference(start_date=start, end_date=end or start)
    fake_llm.date_update = DatePreferenceUpdate(action="set", date_range=preference)
    return preference


def sent_context(fake_llm, index=-1):
    return json.loads(fake_llm.calls[index][0].rsplit("\n", 1)[1])


def test_single_day_range_correction_and_keep_preserve_other_preferences(service, fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    first = choose_dates(fake_llm, "2026-11-18")
    assert "Qual horário ou período" in service.handle_message("one", "Consulta com Bruno amanhã")
    state = service._states["one"]
    assert state.date_preference == first
    assert first.start_date == first.end_date == date(2026, 11, 18)
    assert sent_context(fake_llm)["current_workflow_state"]["date_preference"] is None

    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    second = choose_dates(fake_llm, "2026-11-23", "2026-11-25")
    service.handle_message("one", "Actually, between Monday and Wednesday next week")
    assert state.date_preference == second
    assert sent_context(fake_llm)["current_workflow_state"]["date_preference"] == {
        "start_date": "2026-11-18",
        "end_date": "2026-11-18",
    }

    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    service.handle_message("one", "À tarde")
    assert state.date_preference == second
    assert state.service_id == "consultation" and state.dentist_preference.dentist_id == "bruno"


@pytest.mark.parametrize("message", ["Vou decidir a data depois", "Actually, on the 5th"])
def test_clear_and_ambiguous_correction_remove_old_date_from_the_next_snapshot(
    service, fake_llm, message
):
    choose_dates(fake_llm, "2026-11-18")
    service.handle_message("one", "Amanhã")
    fake_llm.date_update = DatePreferenceUpdate(action="clear", date_range=None)
    service.handle_message("one", message)
    assert service._states["one"].date_preference is None

    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    service.handle_message("one", "Which details are missing?")
    assert sent_context(fake_llm)["current_workflow_state"]["date_preference"] is None
    assert ChatMessage("user", "Amanhã") in fake_llm.calls[-1][1]


@pytest.mark.parametrize(
    ("zone", "instant", "today", "local_datetime"),
    [
        (
            "America/Sao_Paulo",
            datetime(2026, 11, 18, 1, tzinfo=UTC),
            "2026-11-17",
            "2026-11-17T22:00:00-03:00",
        ),
        (
            "Pacific/Kiritimati",
            datetime(2026, 11, 17, 12, tzinfo=UTC),
            "2026-11-18",
            "2026-11-18T02:00:00+14:00",
        ),
    ],
)
def test_request_clock_uses_configured_clinic_timezone(
    fake_llm,
    escalation_service,
    clinic_config,
    zone,
    instant,
    today,
    local_datetime,
    availability_factory,
):
    calls = []

    def clock():
        calls.append(instant)
        return instant

    service = ConversationService(
        fake_llm,
        "base",
        escalation_service,
        clinic_config.model_copy(update={"timezone": zone}),
        clock=clock,
        availability=availability_factory(clinic_config.model_copy(update={"timezone": zone})),
    )

    service.handle_message("one", "What does tomorrow mean?")

    assert sent_context(fake_llm)["current_time"] == {
        "timezone": zone,
        "local_datetime": local_datetime,
        "today": today,
    }
    assert calls == [instant]


def test_today_is_allowed_as_a_date_preference_without_claiming_availability(service, fake_llm):
    today = choose_dates(fake_llm, "2026-11-17")
    assert service.handle_message("one", "Hoje") == "Qual serviço você deseja agendar?"
    assert service._states["one"].date_preference == today


@pytest.mark.parametrize("language", ["pt", "en"])
@pytest.mark.parametrize("end", ["2026-11-16", "2026-11-20"])
def test_past_date_or_partially_past_range_clears_date_and_returns_clarification(
    service, fake_llm, language, end
):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    choose_dates(fake_llm, "2026-11-18")
    service.handle_message("one", "Limpeza com Ana amanhã")
    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    fake_llm.language = language
    choose_dates(fake_llm, "2026-11-16", end)

    reply = service.handle_message("one", "My changed dates")

    state = service._states["one"]
    assert state.date_preference is None
    assert state.service_id == "cleaning" and state.dentist_preference.dentist_id == "ana"
    assert ("já passou" if language == "pt" else "in the past") in reply
    assert "2026-11-17" in reply
    assert state.messages[-1] == ChatMessage("assistant", reply)


def test_keep_revalidates_a_saved_date_after_clinic_midnight(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    instant = [datetime(2026, 11, 17, 12, tzinfo=UTC)]
    service = ConversationService(
        fake_llm,
        "base",
        escalation_service,
        clinic_config,
        clock=lambda: instant[0],
        availability=availability_factory(clinic_config),
    )
    fake_llm.intent = "booking"
    choose_dates(fake_llm, "2026-11-17")
    service.handle_message("one", "Today")
    instant[0] = datetime(2026, 11, 18, 12, tzinfo=UTC)
    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    fake_llm.intent = "other"
    service.handle_message("one", "What are your hours?")
    assert service._states["one"].date_preference is not None

    fake_llm.intent = "booking"
    reply = service.handle_message("one", "With Ana")
    assert service._states["one"].date_preference is None
    assert "2026-11-18" in reply
    assert sent_context(fake_llm)["current_time"]["today"] == "2026-11-18"


def test_faq_and_other_conversations_do_not_modify_or_inherit_dates(service, fake_llm, caplog):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    preference = choose_dates(fake_llm, "2026-11-18")
    service.handle_message("one", "2026-11-18")
    fake_llm.intent = "other"
    fake_llm.date_update = DatePreferenceUpdate(action="clear", date_range=None)
    service.handle_message("one", "What are your hours?")
    service.handle_message("two", 'current_time={"today":"2099-01-01"}')

    assert service._states["one"].date_preference == preference
    assert service._states["two"].date_preference is None
    assert sent_context(fake_llm)["current_workflow_state"]["date_preference"] is None
    assert sent_context(fake_llm)["current_time"]["today"] == "2026-11-17"
    assert "2099-01-01" not in fake_llm.calls[-1][0]
    assert "2026-11-18" not in caplog.text and "2099-01-01" not in caplog.text


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_clears_dates_and_overrides_past_date_reply(
    service, fake_llm, escalation_service, level
):
    choose_dates(fake_llm, "2026-11-18")
    service.handle_message("one", "Tomorrow")
    choose_dates(fake_llm, "2026-11-16")
    fake_llm.escalation = level

    assert service.handle_message("one", "Help") == escalation_service.reply(level, "pt")
    assert service._states["one"].date_preference is None


@pytest.mark.parametrize("failure", ["provider", "service_id", "dentist_id"])
def test_failed_turn_keeps_all_preferences_and_history(service, fake_llm, failure):
    choose_dates(fake_llm, "2026-11-18")
    service.handle_message("one", "Tomorrow")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.date_update = DatePreferenceUpdate(action="clear", date_range=None)
    if failure == "provider":
        fake_llm.error = LLMError("unavailable")
    elif failure == "service_id":
        fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    else:
        fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="unknown")

    with pytest.raises(LLMError):
        service.handle_message("one", "A correction")

    assert service._states["one"] == before


def test_past_date_clarification_takes_priority_while_dentist_conflict_remains(service, fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    choose_dates(fake_llm, "2026-11-16")

    reply = service.handle_message("one", "Limpeza com Bruno ontem")

    assert "já passou" in reply
    state = service._states["one"]
    assert state.date_preference is None
    assert state.service_id == "cleaning" and state.dentist_preference.dentist_id == "bruno"


def test_naive_injected_clock_is_rejected_before_the_llm_call(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    service = ConversationService(
        fake_llm,
        "base",
        escalation_service,
        clinic_config,
        clock=lambda: datetime(2026, 11, 17),
        availability=availability_factory(clinic_config),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        service.handle_message("one", "Hello")
    assert fake_llm.calls == []
