import logging

import pytest

from app.llm.client import ChatMessage, LLMError
from app.services.conversation import ConversationService


@pytest.fixture
def service(fake_llm, escalation_service) -> ConversationService:
    return ConversationService(fake_llm, "test system prompt", escalation_service)


def test_first_message_is_sent_with_the_system_prompt(service, fake_llm):
    reply = service.handle_message("conv-1", "hello")

    assert reply == "reply 1"
    assert fake_llm.calls == [("test system prompt", [ChatMessage("user", "hello")])]


def test_second_message_includes_the_history(service, fake_llm):
    service.handle_message("conv-1", "hello")
    service.handle_message("conv-1", "are you open on saturday?")

    _, messages = fake_llm.calls[1]
    assert messages == [
        ChatMessage("user", "hello"),
        ChatMessage("assistant", "reply 1"),
        ChatMessage("user", "are you open on saturday?"),
    ]


def test_conversations_do_not_mix(service, fake_llm):
    service.handle_message("conv-1", "hello from 1")
    service.handle_message("conv-2", "hello from 2")

    _, messages = fake_llm.calls[1]
    assert messages == [ChatMessage("user", "hello from 2")]


def test_failed_call_leaves_history_unchanged(service, fake_llm):
    service.handle_message("conv-1", "hello")
    fake_llm.error = LLMError("boom")
    with pytest.raises(LLMError):
        service.handle_message("conv-1", "this one fails")

    fake_llm.error = None
    service.handle_message("conv-1", "try again")

    _, messages = fake_llm.calls[2]
    assert messages == [
        ChatMessage("user", "hello"),
        ChatMessage("assistant", "reply 1"),
        ChatMessage("user", "try again"),  # the failed message was not saved
    ]


PATIENT_TEXT = "Meu nome é Maria Silva e meu dente dói"


def test_logs_one_line_without_patient_text(service, caplog):
    caplog.set_level(logging.INFO, logger="app.services.conversation")

    service.handle_message("conv-1", PATIENT_TEXT)

    [record] = caplog.records  # exactly one line per message
    line = record.getMessage()
    assert record.levelno == logging.INFO
    assert "conversation_id=conv-1 outcome=ok" in line
    assert "model=fake-model input_tokens=10 output_tokens=5" in line
    assert "Maria" not in line and "reply 1" not in line


def test_logs_failures_without_patient_text(service, fake_llm, caplog):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    fake_llm.error = LLMError("Could not reach Claude API: timeout")

    with pytest.raises(LLMError):
        service.handle_message("conv-1", PATIENT_TEXT)

    [record] = caplog.records
    line = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "outcome=error" in line and "timeout" in line
    assert "Maria" not in line


@pytest.mark.parametrize("level", ["urgent", "emergency"])
@pytest.mark.parametrize("language", ["pt", "en"])
def test_llm_decision_selects_guidance_and_saves_the_actual_reply(
    service, fake_llm, escalation_service, level, language, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    fake_llm.escalation = level
    fake_llm.language = language

    reply = service.handle_message("conv-1", PATIENT_TEXT)

    assert len(fake_llm.calls) == 1  # The LLM classifies; no second classification call.
    assert reply == escalation_service.reply(level, language)
    assert "reply 1" not in reply  # Escalation replaces any free-form model reply.
    assert f"escalation={level}" in caplog.records[0].getMessage()
    assert PATIENT_TEXT not in caplog.text

    fake_llm.escalation = "none"
    service.handle_message("conv-1", "Follow-up")
    assert fake_llm.calls[1][1][1] == ChatMessage("assistant", reply)


def test_no_keyword_matching_overrides_the_llm_decision(service, fake_llm):
    reply = service.handle_message("conv-1", "Não tenho dificuldade para respirar.")
    assert reply == "reply 1"
    assert len(fake_llm.calls) == 1


def test_booking_intent_starts_collecting_preferences_and_isolates_other_conversations(
    service, fake_llm, caplog
):
    caplog.set_level(logging.INFO, logger="app.services.conversation")
    fake_llm.intent = "booking"

    service.handle_message("booking", "Quero marcar uma limpeza")

    assert service._states["booking"].stage == "collecting_preferences"
    assert "intent=booking stage=collecting_preferences" in caplog.text

    fake_llm.intent = "other"
    service.handle_message("other", "What are your hours?")

    assert service._states["other"].stage == "chatting"
    assert service._states["booking"].stage == "collecting_preferences"


def test_unrelated_faq_preserves_the_pending_booking_stage(service, fake_llm):
    fake_llm.intent = "booking"
    service.handle_message("conv-1", "I'd like an appointment")

    fake_llm.intent = "other"
    reply = service.handle_message("conv-1", "Where is the clinic?")

    assert reply == "reply 2"
    assert service._states["conv-1"].stage == "collecting_preferences"
    assert service._states["conv-1"].messages[-1] == ChatMessage("assistant", reply)


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_escalation_stops_preference_collection_even_with_booking_intent(
    service, fake_llm, escalation_service, level
):
    fake_llm.intent = "booking"
    service.handle_message("conv-1", "I'd like an appointment")
    fake_llm.escalation = level

    reply = service.handle_message("conv-1", "Help, I need an appointment urgently")

    assert reply == escalation_service.reply(level, "pt")
    assert service._states["conv-1"].stage == "chatting"
    assert service._states["conv-1"].messages[-1] == ChatMessage("assistant", reply)


@pytest.mark.parametrize("initial_intent", ["other", "booking"])
def test_failed_interpretation_preserves_stage_and_history(service, fake_llm, initial_intent):
    fake_llm.intent = initial_intent
    service.handle_message("conv-1", "First message")
    state = service._states["conv-1"]
    previous_stage = state.stage
    previous_messages = list(state.messages)

    fake_llm.intent = "booking" if initial_intent == "other" else "other"
    fake_llm.escalation = "emergency"
    fake_llm.error = LLMError("interpretation unavailable")

    with pytest.raises(LLMError):
        service.handle_message("conv-1", "Next message")

    assert state.stage == previous_stage
    assert state.messages == previous_messages
