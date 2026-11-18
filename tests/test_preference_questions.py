"""Backend questions depend on accepted preferences, never on parsing patient keywords."""

from dataclasses import replace

import pytest

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
from app.services.preferences import PreferenceStatus, missing_preference_reply


@pytest.mark.parametrize(
    "missing,pt,en",
    [
        (("service", "dentist", "date", "time"), "Qual serviço", "Which service"),
        (
            ("dentist", "date", "time"),
            "Você tem preferência por um dentista",
            "Do you prefer a particular dentist",
        ),
        (("date", "time"), "Qual data ou intervalo", "Which date or date range"),
        (("time",), "Qual horário ou período", "What time or part of the day"),
    ],
)
@pytest.mark.parametrize("language", ["pt", "en"])
def test_question_asks_only_for_first_missing_detail(missing, pt, en, language):
    reply = missing_preference_reply(
        PreferenceStatus(missing=missing, conflicts=()),
        language,
        eligible_dentists=["Demo Dentist"],
    )
    assert reply.startswith(pt if language == "pt" else en)
    assert reply.count("?") == 1
    assert ("Demo Dentist" in reply) == (missing[0] == "dentist")


@pytest.mark.parametrize(
    "status",
    [
        PreferenceStatus(missing=(), conflicts=()),
        PreferenceStatus(missing=("date",), conflicts=("service_dentist",)),
    ],
)
def test_complete_and_conflicting_drafts_do_not_get_generic_questions(status):
    assert missing_preference_reply(status, "pt", eligible_dentists=[]) is None


@pytest.fixture
def service(fake_llm, escalation_service, clinic_config, fixed_clock):
    fake_llm.intent = "booking"
    return ConversationService(
        fake_llm, "base prompt", escalation_service, clinic_config, clock=fixed_clock
    )


def keep_preferences(fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="keep", service_id=None)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="keep", dentist_id=None)
    fake_llm.date_update = DatePreferenceUpdate(action="keep", date_range=None)
    fake_llm.time_update = TimePreferenceUpdate(action="keep", preference=None)


@pytest.mark.parametrize("language", ["pt", "en"])
def test_multi_turn_collection_asks_only_missing_details_and_saves_actual_replies(
    service, fake_llm, language
):
    fake_llm.language = language
    first = service.handle_message("one", "Book")
    assert first.startswith("Qual serviço" if language == "pt" else "Which service")
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    second = service.handle_message("one", "A consultation")
    assert "Dr. Ana Souza" in second and "Dr. Bruno Lima" in second
    assert "Dr. Carla Mendes" not in second
    keep_preferences(fake_llm)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    third = service.handle_message("one", "Any eligible dentist")
    assert third.startswith("Qual data" if language == "pt" else "Which date")
    keep_preferences(fake_llm)
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-18", end_date="2026-11-20")
    )
    fourth = service.handle_message("one", "Between tomorrow and Friday")
    assert fourth.startswith("Qual horário" if language == "pt" else "What time")
    keep_preferences(fake_llm)
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="any", start_time=None, end_time=None)
    )
    assert service.handle_message("one", "Any time") == "reply 5"
    assert len(fake_llm.calls) == 5
    state = service._states["one"]
    assert state.stage == "collecting_preferences"
    assert [m.content for m in state.messages if m.role == "assistant"] == [
        first,
        second,
        third,
        fourth,
        "reply 5",
    ]
    assert fake_llm.calls[1][1][1] == ChatMessage("assistant", first)
    assert fake_llm.calls[4][1][-2] == ChatMessage("assistant", fourth)


def test_later_fields_supplied_early_are_not_requested_again(service, fake_llm):
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-18", end_date="2026-11-18")
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )
    assert service.handle_message("one", "Tomorrow morning") == "Qual serviço você deseja agendar?"
    keep_preferences(fake_llm)
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    assert "Profissionais habilitados: Dr. Ana Souza." in service.handle_message(
        "one", "A cleaning"
    )
    keep_preferences(fake_llm)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    assert service.handle_message("one", "With Ana") == "reply 3"


@pytest.mark.parametrize("field", ["service", "dentist", "date", "time"])
def test_clear_preserves_specific_model_clarification_then_resumes_missing_question(
    service, fake_llm, field, monkeypatch
):
    service.handle_message("one", "Book")
    generate = fake_llm.generate

    def clarify(system, messages):
        return replace(generate(system, messages), text="Please clarify the corrected preference.")

    monkeypatch.setattr(fake_llm, "generate", clarify)
    updates = {
        "service": ServicePreferenceUpdate(action="clear", service_id=None),
        "dentist": DentistPreferenceUpdate(action="clear", dentist_id=None),
        "date": DatePreferenceUpdate(action="clear", date_range=None),
        "time": TimePreferenceUpdate(action="clear", preference=None),
    }
    setattr(fake_llm, field + "_update", updates[field])
    reply = service.handle_message("one", "Ambiguous correction")
    assert reply == "Please clarify the corrected preference."
    assert service._states["one"].messages[-1] == ChatMessage("assistant", reply)
    keep_preferences(fake_llm)
    assert service.handle_message("one", "Continue") == "Qual serviço você deseja agendar?"


def test_faq_is_answered_while_draft_is_preserved_then_collection_resumes(service, fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    first = service.handle_message("one", "A cleaning")
    fake_llm.intent = "other"
    fake_llm.service_update = ServicePreferenceUpdate(action="clear", service_id=None)
    assert service.handle_message("one", "What are your hours?") == "reply 2"
    assert service._states["one"].service_id == "cleaning"
    fake_llm.intent = "booking"
    keep_preferences(fake_llm)
    assert service.handle_message("one", "Continue") == first


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_takes_priority_over_missing_questions(
    service, fake_llm, escalation_service, level
):
    service.handle_message("one", "Book")
    fake_llm.escalation = level
    assert service.handle_message("one", "Help") == escalation_service.reply(level, "pt")
    assert service._states["one"].stage == "chatting"


@pytest.mark.parametrize("problem", ["past_date", "past_time", "dentist_conflict"])
def test_backend_validation_clarifications_take_priority_over_generic_questions(
    service, fake_llm, problem
):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    if problem == "past_date":
        day = "2026-11-16"
    else:
        day = "2026-11-17"
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date=day, end_date=day)
    )
    if problem == "past_time":
        fake_llm.time_update = TimePreferenceUpdate(
            action="set",
            preference=TimePreference(kind="window", start_time="08:00", end_time="08:00"),
        )
    if problem == "dentist_conflict":
        fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")
    reply = service.handle_message("one", "My preferences")
    assert ("já passou" if problem != "dentist_conflict" else "não oferece") in reply


@pytest.mark.parametrize("failure", ["provider", "service_id"])
def test_failed_turn_preserves_question_history_and_next_success_recovers(
    service, fake_llm, failure
):
    first = service.handle_message("one", "Book")
    before = service._states["one"].model_copy(deep=True)
    if failure == "provider":
        fake_llm.error = LLMError("unavailable")
    else:
        fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")
    with pytest.raises(LLMError):
        service.handle_message("one", "Failed turn")
    assert service._states["one"] == before
    fake_llm.error = None
    keep_preferences(fake_llm)
    assert service.handle_message("one", "Continue") == first
    assert ChatMessage("user", "Failed turn") not in fake_llm.calls[-1][1]


def test_other_conversations_start_with_their_own_missing_question(service, fake_llm):
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    assert "dentista" in service.handle_message("one", "A cleaning")
    keep_preferences(fake_llm)
    assert service.handle_message("two", "Book") == "Qual serviço você deseja agendar?"


def test_dentist_question_uses_current_configured_names_and_eligibility(
    fake_llm, escalation_service, clinic_config, fixed_clock
):
    clinic = clinic_config.model_copy(
        update={
            "dentists": [
                dentist.model_copy(update={"name": "Dr. Demo"})
                if dentist.id == "bruno"
                else dentist
                for dentist in clinic_config.dentists
            ],
            "services": [
                service.model_copy(update={"dentist_ids": ["bruno"]})
                if service.id == "consultation"
                else service
                for service in clinic_config.services
            ],
        }
    )
    service = ConversationService(fake_llm, "prompt", escalation_service, clinic, clock=fixed_clock)
    fake_llm.intent = "booking"
    fake_llm.language = "en"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    reply = service.handle_message("one", "A consultation")
    assert reply.endswith("Eligible dentists: Dr. Demo.")
    assert "Dr. Ana Souza" not in reply and "Dr. Bruno Lima" not in reply
    assert service._states["one"].dentist_preference.kind == "unspecified"


def test_mixed_booking_and_clinic_question_keeps_answer_and_one_backend_follow_up(
    service, fake_llm
):
    fake_llm.language = "en"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.booking_answer = "I do not have pricing information; please call the clinic."
    reply = service.handle_message("one", "How much is a cleaning? I'd like to schedule one.")
    answer, question = reply.split("\n\n")
    assert answer == fake_llm.booking_answer
    assert question.startswith("Do you prefer a particular dentist")
    assert "Dr. Ana Souza" in question
    assert "reply 1" not in reply and reply.count("?") == 1
    assert service._states["one"].service_id == "cleaning"
    assert service._states["one"].messages[-1] == ChatMessage("assistant", reply)
    fake_llm.booking_answer = None
    keep_preferences(fake_llm)
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    assert service.handle_message("one", "Any eligible dentist").startswith("Which date")
    assert fake_llm.calls[-1][1][-2] == ChatMessage("assistant", reply)
