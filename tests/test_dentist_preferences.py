"""Dentist preferences and compatibility use configured facts and fake LLM decisions."""

import json
import logging

import pytest
from pydantic import ValidationError

from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import (
    DentistPreference,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
)
from app.services.conversation import ConversationService


@pytest.fixture
def service(fake_llm, escalation_service, clinic_config, availability_factory):
    fake_llm.intent = "booking"
    return ConversationService(
        fake_llm,
        "base prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )


def test_named_dentist_can_be_kept_corrected_cleared_or_replaced_with_any(service, fake_llm):
    assert service._states == {}
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    service.handle_message("one", "Prefiro a Dra. Ana")
    state = service._states["one"]
    assert state.dentist_preference == DentistPreference(kind="specific", dentist_id="ana")

    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    service.handle_message("one", "Amanhã de manhã")
    assert state.dentist_preference.dentist_id == "ana"

    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    service.handle_message("one", "Actually, Dr. Bruno")
    assert state.dentist_preference.dentist_id == "bruno"

    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    service.handle_message("one", "Pode ser qualquer dentista")
    assert state.dentist_preference == DentistPreference(kind="any")

    fake_llm.dentist_update = DentistPreferenceUpdate(action="clear", dentist_id=None)
    service.handle_message("one", "Vou decidir o profissional depois")
    assert state.dentist_preference == DentistPreference()
    snapshots = [
        json.loads(prompt.rsplit("\n", 1)[1])["current_workflow_state"]["dentist_preference"]
        for prompt, _ in fake_llm.calls
    ]
    assert snapshots == [
        {"kind": "unspecified", "dentist_id": None},
        {"kind": "specific", "dentist_id": "ana"},
        {"kind": "specific", "dentist_id": "ana"},
        {"kind": "specific", "dentist_id": "bruno"},
        {"kind": "any", "dentist_id": None},
    ]


@pytest.mark.parametrize("action", ["keep", "any"])
def test_single_eligible_dentist_does_not_automatically_become_patient_preference(
    service, fake_llm, action
):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action=action, dentist_id=None)

    reply = service.handle_message("one", "Quero uma limpeza")

    if action == "keep":
        assert "Dr. Ana Souza" in reply and "preferência por um dentista" in reply
    else:
        assert reply == "Qual data ou intervalo de datas você prefere?"
    assert service._states["one"].dentist_preference == DentistPreference(
        kind="any" if action == "any" else "unspecified"
    )


@pytest.mark.parametrize("language", ["pt", "en"])
def test_incompatible_pair_returns_configured_names_and_preserves_requested_choices(
    service, fake_llm, language
):
    fake_llm.language = language
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")

    reply = service.handle_message("one", "A cleaning with Dr. Bruno")

    state = service._states["one"]
    assert state.service_id == "cleaning"
    assert state.dentist_preference == DentistPreference(kind="specific", dentist_id="bruno")
    assert state.stage == "collecting_preferences"
    assert "Dr. Bruno Lima" in reply and "Dr. Ana Souza" in reply and "Cleaning" in reply
    assert "Dr. Carla Mendes" not in reply
    assert (
        "mudar o profissional ou o serviço"
        if language == "pt"
        else "change the dentist or the service"
    ) in reply
    assert ("não posso agendar" if language == "pt" else "cannot book appointments yet") in reply
    assert state.messages[-1] == ChatMessage("assistant", reply)


@pytest.mark.parametrize("resolution", ["service", "dentist", "any", "clear"])
def test_service_change_rechecks_retained_dentist_and_patient_can_resolve_conflict(
    service, fake_llm, resolution
):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    assert service.handle_message("one", "A consultation with Bruno") == (
        "Qual data ou intervalo de datas você prefere?"
    )

    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    assert "Dr. Ana Souza" in service.handle_message("one", "Actually, a cleaning")
    assert service._states["one"].dentist_preference.dentist_id == "bruno"

    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    assert "Dr. Ana Souza" in service.handle_message("one", "Tomorrow?")
    if resolution == "service":
        fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    else:
        fake_llm.dentist_update = DentistPreferenceUpdate(
            action="set" if resolution == "dentist" else resolution,
            dentist_id="ana" if resolution == "dentist" else None,
        )
    reply = service.handle_message("one", "My new choice")
    assert reply == (
        "reply 4" if resolution == "clear" else "Qual data ou intervalo de datas você prefere?"
    )


def test_dentist_can_be_supplied_before_service_and_conflict_is_checked_when_service_arrives(
    service, fake_llm
):
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="carla")
    assert service.handle_message("one", "With Dr. Carla") == "Qual serviço você deseja agendar?"
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")

    reply = service.handle_message("one", "An initial consultation")

    assert "Dr. Ana Souza, Dr. Bruno Lima" in reply
    assert service._states["one"].dentist_preference.dentist_id == "carla"


def test_unknown_dentist_rejects_both_updates_without_changing_history_or_exposing_id(
    service, fake_llm, caplog
):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    service.handle_message("one", "A consultation with Bruno")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="private_unknown")

    with pytest.raises(LLMError, match="unknown dentist ID"):
        service.handle_message("one", "A different service and dentist")

    assert service._states["one"] == before
    assert "private_unknown" not in caplog.text
    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    service.handle_message("one", "Try again")
    prompt, messages = fake_llm.calls[-1]
    snapshot = json.loads(prompt.rsplit("\n", 1)[1])["current_workflow_state"]
    assert snapshot["dentist_preference"] == {"kind": "specific", "dentist_id": "bruno"}
    assert ChatMessage("user", "A different service and dentist") not in messages


def test_faq_preserves_dentist_preference_and_other_conversations_start_unspecified(
    service, fake_llm, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    service.handle_message("one", "With Dr. Bruno")
    fake_llm.intent = "other"
    fake_llm.dentist_update = DentistPreferenceUpdate(action="clear", dentist_id=None)
    assert service.handle_message("one", "What are your hours?") == "reply 2"
    service.handle_message("two", "Hello")

    assert service._states["one"].dentist_preference.dentist_id == "bruno"
    assert service._states["two"].dentist_preference == DentistPreference()
    assert "bruno" not in caplog.text and "Dr. Bruno" not in caplog.text


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_ignores_invalid_proposals_and_clears_dentist_for_the_next_request(
    service, fake_llm, escalation_service, level
):
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    service.handle_message("one", "With Bruno")
    fake_llm.escalation = level
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="unknown")
    assert service.handle_message("one", "Help") == escalation_service.reply(level, "pt")
    assert service._states["one"].dentist_preference == DentistPreference()
    fake_llm.escalation = "none"
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    service.handle_message("one", "Hello")
    prompt, _ = fake_llm.calls[-1]
    snapshot = json.loads(prompt.rsplit("\n", 1)[1])["current_workflow_state"]
    assert snapshot["dentist_preference"] == {"kind": "unspecified", "dentist_id": None}


def test_provider_failure_does_not_clear_dentist_preference(service, fake_llm):
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    service.handle_message("one", "With Ana")
    before = service._states["one"].model_copy(deep=True)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    fake_llm.error = LLMError("unavailable")

    with pytest.raises(LLMError):
        service.handle_message("one", "Actually, anyone")

    assert service._states["one"] == before


@pytest.mark.parametrize(
    "preference",
    [
        {"kind": "specific", "dentist_id": None},
        {"kind": "any", "dentist_id": "ana"},
        {"kind": "unspecified", "dentist_id": "ana"},
    ],
)
def test_saved_preference_rejects_contradictory_states(preference):
    with pytest.raises(ValidationError):
        DentistPreference.model_validate(preference)
