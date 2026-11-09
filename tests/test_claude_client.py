"""ClaudeClient tests with a stub in place of the Anthropic SDK: no network, no API key, no cost."""

from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.llm.claude import ClaudeClient
from app.llm.client import ChatMessage, LLMError

_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


class StubSDK:
    """Mimics the one SDK call ClaudeClient makes: sdk.beta.messages.create(**kwargs)."""

    def __init__(self, response=None, error=None):
        self.calls = []
        self._response = response
        self._error = error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def make_response(stop_reason="end_turn", text="Hello!"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        content=[
            SimpleNamespace(type="thinking", thinking=""),  # thinking blocks must be ignored
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(input_tokens=120, output_tokens=15),
    )


def make_client(sdk):
    return ClaudeClient(sdk, model="claude-opus-5-5", effort="low", max_tokens=1000)


def test_sends_request_and_returns_text_and_usage():
    sdk = StubSDK(response=make_response(text="We open at 8am."))
    history = [
        ChatMessage("user", "Hi"),
        ChatMessage("assistant", "Hello! How can I help?"),
        ChatMessage("user", "When do you open?"),
    ]

    result = make_client(sdk).generate(system="You are a receptionist.", messages=history)

    assert result.text == "We open at 8am."
    assert (result.input_tokens, result.output_tokens) == (120, 15)

    sent = sdk.calls[0]
    assert sent["system"] == "You are a receptionist."
    assert sent["messages"] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "When do you open?"},
    ]
    assert sent["output_config"] == {"effort": "low"}
    assert sent["fallbacks"] == "default"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_unusable_stop_reason_raises(stop_reason):
    sdk = StubSDK(response=make_response(stop_reason=stop_reason))

    with pytest.raises(LLMError):
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])


def test_empty_text_raises():
    sdk = StubSDK(response=make_response(text="   "))

    with pytest.raises(LLMError, match="no text"):
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])


def test_api_error_is_translated():
    error = anthropic.InternalServerError(
        "overloaded", response=httpx2.Response(500, request=_REQUEST), body=None
    )
    sdk = StubSDK(error=error)

    with pytest.raises(LLMError, match="500"):
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])


def test_connection_error_is_translated():
    sdk = StubSDK(error=anthropic.APIConnectionError(request=_REQUEST))

    with pytest.raises(LLMError, match="Could not reach"):
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])
