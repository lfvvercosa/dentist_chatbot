"""ClaudeClient tests with a stub in place of the Anthropic SDK: no network, no API key, no cost."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.llm.claude import ClaudeClient
from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import DentistPreferenceUpdate, ServicePreferenceUpdate
from app.schemas.assistant import AssistantDecision

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


def make_response(
    stop_reason="end_turn",
    text="Hello!",
    escalation="none",
    language="pt",
    raw=None,
    intent="other",
    service_update=None,
    dentist_update=None,
):
    body = json.dumps(
        {
            "reply": text,
            "escalation": escalation,
            "language": language,
            "intent": intent,
            "service_update": (
                service_update
                if service_update is not None
                else {"action": "keep", "service_id": None}
            ),
            "dentist_update": (
                dentist_update
                if dentist_update is not None
                else {"action": "keep", "dentist_id": None}
            ),
        }
    )
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="claude-opus-5-5",
        content=[
            SimpleNamespace(type="thinking", thinking=""),  # thinking blocks must be ignored
            SimpleNamespace(type="text", text=body if raw is None else raw),
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
    assert result.escalation == "none" and result.language == "pt"
    assert result.intent == "other"

    sent = sdk.calls[0]
    assert sent["system"] == "You are a receptionist."
    assert sent["messages"] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello! How can I help?"},
        {"role": "user", "content": "When do you open?"},
    ]
    assert sent["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": AssistantDecision.model_json_schema()},
    }
    assert sent["fallbacks"] == "default"


def test_booking_intent_is_returned_from_the_validated_decision():
    sdk = StubSDK(response=make_response(text="Which day would you prefer?", intent="booking"))

    result = make_client(sdk).generate(
        system="s", messages=[ChatMessage("user", "I would like a cleaning")]
    )

    assert result.intent == "booking"
    assert result.text == "Which day would you prefer?"


def test_service_update_is_returned_from_the_validated_decision():
    sdk = StubSDK(
        response=make_response(
            intent="booking", service_update={"action": "set", "service_id": "cleaning"}
        )
    )

    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "A cleaning")])

    assert result.service_update == ServicePreferenceUpdate(action="set", service_id="cleaning")


@pytest.mark.parametrize(
    ("action", "dentist_id"), [("set", "ana"), ("keep", None), ("clear", None), ("any", None)]
)
def test_dentist_update_is_returned_from_the_validated_decision(action, dentist_id):
    sdk = StubSDK(
        response=make_response(
            intent="booking", dentist_update={"action": action, "dentist_id": dentist_id}
        )
    )

    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "My preference")])

    assert result.dentist_update == DentistPreferenceUpdate(action=action, dentist_id=dentist_id)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens", "tool_use", "pause_turn", None])
def test_unusable_stop_reason_raises(stop_reason):
    sdk = StubSDK(response=make_response(stop_reason=stop_reason))

    with pytest.raises(LLMError):
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])


def test_empty_text_raises():
    sdk = StubSDK(response=make_response(raw="   "))

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


@pytest.mark.parametrize("level", ["urgent", "emergency"])
def test_structured_escalation_allows_empty_normal_reply(level):
    sdk = StubSDK(response=make_response(text="", escalation=level, language="en"))
    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Help")])
    assert result.text == ""
    assert result.escalation == level and result.language == "en"


_VALID_DECISION = {
    "reply": "patient secret",
    "escalation": "none",
    "language": "pt",
    "intent": "other",
    "service_update": {"action": "keep", "service_id": None},
    "dentist_update": {"action": "keep", "dentist_id": None},
}


@pytest.mark.parametrize(
    "body",
    [
        "patient secret: not JSON",
        {"reply": "patient secret"},
        _VALID_DECISION | {"escalation": "invalid"},
        _VALID_DECISION | {"language": "fr"},
        _VALID_DECISION | {"reply": "   "},
        _VALID_DECISION | {"extra": True},
        {k: v for k, v in _VALID_DECISION.items() if k != "intent"},
        _VALID_DECISION | {"intent": "book_now"},
        {k: v for k, v in _VALID_DECISION.items() if k != "service_update"},
        _VALID_DECISION | {"service_update": None},
        _VALID_DECISION | {"service_update": {"action": "set", "service_id": None}},
        _VALID_DECISION | {"service_update": {"action": "clear", "service_id": "cleaning"}},
        _VALID_DECISION | {"service_update": {"action": "keep", "service_id": "cleaning"}},
        _VALID_DECISION | {"service_update": {"action": "set", "service_id": "INVALID ID"}},
        _VALID_DECISION | {"service_update": {"action": "invent", "service_id": None}},
        {k: v for k, v in _VALID_DECISION.items() if k != "dentist_update"},
        _VALID_DECISION | {"dentist_update": None},
        _VALID_DECISION | {"dentist_update": {"action": "set", "dentist_id": None}},
        _VALID_DECISION | {"dentist_update": {"action": "any", "dentist_id": "ana"}},
        _VALID_DECISION | {"dentist_update": {"action": "keep", "dentist_id": "ana"}},
        _VALID_DECISION | {"dentist_update": {"action": "clear", "dentist_id": "ana"}},
        _VALID_DECISION | {"dentist_update": {"action": "set", "dentist_id": "INVALID ID"}},
        _VALID_DECISION | {"dentist_update": {"action": "invent", "dentist_id": None}},
        _VALID_DECISION | {"dentist_update": {"action": "any", "dentist_id": None, "extra": True}},
    ],
)
def test_invalid_decision_is_rejected_without_exposing_content(body):
    raw = body if isinstance(body, str) else json.dumps(body)
    sdk = StubSDK(response=make_response(raw=raw))
    with pytest.raises(LLMError, match="invalid assistant decision") as error:
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])
    assert "patient secret" not in str(error.value)
