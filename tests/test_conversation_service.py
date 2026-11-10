import pytest

from app.llm.client import ChatMessage, LLMError
from app.services.conversation import ConversationService


@pytest.fixture
def service(fake_llm) -> ConversationService:
    return ConversationService(fake_llm, system_prompt="test system prompt")


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
