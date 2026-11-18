"""Completeness uses accepted preferences and configured eligibility, without LLM/calendar I/O."""

import logging

import pytest

from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import (
    DatePreference,
    DatePreferenceUpdate,
    DentistPreference,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.services.conversation import ConversationService
from app.services.preferences import assess_preferences


@pytest.fixture
def preferences(clinic_config):
    return {
        "service": next(service for service in clinic_config.services if service.id == "cleaning"),
        "dentist": DentistPreference(kind="specific", dentist_id="ana"),
        "dates": DatePreference(start_date="2026-11-18", end_date="2026-11-18"),
        "time": TimePreference(kind="morning", start_time=None, end_time=None),
    }


def test_empty_draft_reports_all_missing_fields_in_follow_up_order():
    result = assess_preferences(service=None, dentist=DentistPreference(), dates=None, time=None)
    assert result.missing == ("service", "dentist", "date", "time")
    assert result.conflicts == ()
    assert result.is_complete is False


@pytest.mark.parametrize(
    "argument,value,missing",
    [
        ("service", None, "service"),
        ("dentist", DentistPreference(), "dentist"),
        ("dates", None, "date"),
        ("time", None, "time"),
    ],
)
def test_each_unanswered_preference_blocks_completeness(preferences, argument, value, missing):
    preferences[argument] = value
    result = assess_preferences(**preferences)
    assert result.missing == (missing,)
    assert result.conflicts == ()
    assert result.is_complete is False


@pytest.mark.parametrize("dentist", ["specific", "any"])
@pytest.mark.parametrize("kind", ["morning", "afternoon", "evening", "window", "any"])
def test_named_periods_windows_and_explicit_any_are_answered_preferences(
    preferences, dentist, kind
):
    preferences["dentist"] = DentistPreference(
        kind=dentist, dentist_id="ana" if dentist == "specific" else None
    )
    preferences["time"] = TimePreference(
        kind=kind,
        start_time="14:00" if kind == "window" else None,
        end_time="16:00" if kind == "window" else None,
    )
    result = assess_preferences(**preferences)
    assert result.missing == result.conflicts == ()
    assert result.is_complete is True


def test_complete_date_range_and_exact_time_do_not_require_one_exact_date(preferences):
    preferences["dates"] = DatePreference(start_date="2026-11-18", end_date="2026-11-20")
    preferences["time"] = TimePreference(kind="window", start_time="14:00", end_time="14:00")
    assert assess_preferences(**preferences).is_complete is True


def test_incompatible_filled_draft_is_incomplete_without_mutating_choices(preferences):
    preferences["dentist"] = DentistPreference(kind="specific", dentist_id="bruno")
    before = preferences.copy()
    result = assess_preferences(**preferences)
    assert result.missing == ()
    assert result.conflicts == ("service_dentist",)
    assert result.is_complete is False
    assert preferences == before


def test_conflicts_and_missing_fields_are_reported_together(preferences):
    preferences["dentist"] = DentistPreference(kind="specific", dentist_id="bruno")
    preferences["dates"] = None
    preferences["time"] = None
    result = assess_preferences(**preferences)
    assert result.missing == ("date", "time")
    assert result.conflicts == ("service_dentist",)
    assert result.is_complete is False


def test_compatibility_follows_configured_service_eligibility(preferences):
    preferences["service"] = preferences["service"].model_copy(update={"dentist_ids": ["bruno"]})
    result = assess_preferences(**preferences)
    assert result.conflicts == ("service_dentist",)
    preferences["dentist"] = DentistPreference(kind="specific", dentist_id="bruno")
    assert assess_preferences(**preferences).is_complete is True


@pytest.fixture
def service(fake_llm, escalation_service, clinic_config, fixed_clock, caplog):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    fake_llm.intent = "booking"
    return ConversationService(
        fake_llm, "base prompt", escalation_service, clinic_config, clock=fixed_clock
    )


def fill_draft(fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-18", end_date="2026-11-18")
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )


def keep_preferences(fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)


def test_service_assesses_merged_preferences_and_logs_only_field_codes(service, fake_llm, caplog):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    assert "Profissionais habilitados: Dr. Ana Souza." in service.handle_message(
        "one", "Private patient text"
    )
    first = caplog.records[-1].getMessage()
    assert "preferences_complete=False missing_preferences=dentist,date,time" in first
    assert "Private patient text" not in caplog.text
    fill_draft(fake_llm)
    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    assert service.handle_message("one", "Any dentist tomorrow morning") == "reply 2"
    assert (
        "preferences_complete=True missing_preferences=none preference_conflicts=none"
        in caplog.text
    )
    state = service._states["one"]
    assert state.stage == "collecting_preferences"
    assert "preferences_complete" not in state.model_dump()
    assert state.messages[-1] == ChatMessage("assistant", "reply 2")


def test_corrections_recheck_compatibility_and_clear_reopens_collection(service, fake_llm, caplog):
    fill_draft(fake_llm)
    service.handle_message("one", "A cleaning tomorrow morning")
    keep_preferences(fake_llm)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    assert "Dr. Ana Souza" in service.handle_message("one", "With Bruno")
    assert (
        "preferences_complete=False missing_preferences=none preference_conflicts=service_dentist"
        in caplog.records[-1].getMessage()
    )
    assert service._states["one"].dentist_preference.dentist_id == "bruno"
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    service.handle_message("one", "Any eligible dentist")
    assert "preferences_complete=True" in caplog.records[-1].getMessage()
    keep_preferences(fake_llm)
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    service.handle_message("one", "I'll decide the time later")
    assert "preferences_complete=False missing_preferences=time" in caplog.records[-1].getMessage()


@pytest.mark.parametrize("expired", ["date", "time"])
def test_status_uses_preferences_after_date_and_time_rejection(service, fake_llm, caplog, expired):
    fill_draft(fake_llm)
    day = "2026-11-16" if expired == "date" else "2026-11-17"
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date=day, end_date=day)
    )
    if expired == "time":
        fake_llm.time_update = TimePreferenceUpdate(
            action="set",
            preference=TimePreference(kind="window", start_time="08:00", end_time="08:00"),
        )
    assert "já passou" in service.handle_message("one", "My preferences")
    assert (
        f"preferences_complete=False missing_preferences={expired}"
        in caplog.records[-1].getMessage()
    )


def test_failed_turn_cannot_report_a_new_status_or_erase_the_complete_draft(
    service, fake_llm, caplog
):
    fill_draft(fake_llm)
    service.handle_message("one", "Complete draft")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="private_unknown")
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    with pytest.raises(LLMError):
        service.handle_message("one", "Rejected correction")
    assert service._states["one"] == before
    assert "preferences_complete" not in caplog.records[-1].getMessage()
    assert "private_unknown" not in caplog.text
    keep_preferences(fake_llm)
    service.handle_message("one", "Continue")
    assert "preferences_complete=True" in caplog.records[-1].getMessage()


@pytest.mark.parametrize("route", ["faq", "urgent", "emergency"])
def test_faq_and_escalation_skip_completeness_and_do_not_apply_proposals(
    service, fake_llm, caplog, escalation_service, route
):
    fill_draft(fake_llm)
    service.handle_message("one", "Complete draft")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.time_update = TimePreferenceUpdate(action="clear", preference=None)
    if route == "faq":
        fake_llm.intent = "other"
    else:
        fake_llm.escalation = route
    reply = service.handle_message("one", "Other request")
    assert (
        "preferences_complete=None missing_preferences=None preference_conflicts=None"
        in caplog.records[-1].getMessage()
    )
    state = service._states["one"]
    if route == "faq":
        assert state.time_preference == before.time_preference
        assert state.stage == "collecting_preferences"
    else:
        assert reply == escalation_service.reply(route, "pt")
        assert state.service_id is None and state.dentist_preference.kind == "unspecified"
        assert state.date_preference is None and state.time_preference is None
        assert state.stage == "chatting"


def test_another_conversation_has_its_own_missing_preferences(service, fake_llm, caplog):
    fill_draft(fake_llm)
    service.handle_message("one", "Complete draft")
    keep_preferences(fake_llm)
    service.handle_message("two", "Book")
    assert (
        "preferences_complete=False missing_preferences=service,dentist,date,time"
        in caplog.records[-1].getMessage()
    )
