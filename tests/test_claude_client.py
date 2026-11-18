"""ClaudeClient tests with a stub in place of the Anthropic SDK: no network, no API key, no cost."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.llm.claude import ClaudeClient
from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import (
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.schemas.assistant import AssistantDecision
from app.services.conversation import ConversationService

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
    date_update=None,
    time_update=None,
    booking_answer=None,
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
            "date_update": (
                date_update if date_update is not None else {"action": "keep", "date_range": None}
            ),
            "time_update": (
                time_update if time_update is not None else {"action": "keep", "preference": None}
            ),
            "booking_answer": booking_answer,
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


@pytest.mark.parametrize("answer", [None, "We open at 08:00 on Mondays."])
def test_booking_answer_is_returned_from_the_validated_decision(answer):
    sdk = StubSDK(response=make_response(intent="booking", booking_answer=answer))
    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "My request")])
    assert result.booking_answer == answer


def test_real_adapter_and_service_preserve_mixed_answer_before_backend_question(
    escalation_service, clinic_config, fixed_clock
):
    sdk = StubSDK(
        response=make_response(
            intent="booking",
            language="en",
            text="Unselected full model reply",
            booking_answer="We open at 08:00 on Mondays.",
        )
    )
    service = ConversationService(
        make_client(sdk), "prompt", escalation_service, clinic_config, clock=fixed_clock
    )
    reply = service.handle_message("one", "Can I book? When do you open on Monday?")
    assert reply == "We open at 08:00 on Mondays.\n\nWhich service would you like to schedule?"
    assert len(sdk.calls) == 1
    assert service._states["one"].messages[-1] == ChatMessage("assistant", reply)


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


@pytest.mark.parametrize("action", ["set", "keep", "clear"])
def test_date_update_is_returned_as_validated_calendar_dates(action):
    date_range = {"start_date": "2026-11-18", "end_date": "2026-11-20"} if action == "set" else None
    sdk = StubSDK(response=make_response(date_update={"action": action, "date_range": date_range}))

    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "My dates")])

    expected = DatePreference(**date_range) if date_range is not None else None
    assert result.date_update == DatePreferenceUpdate(action=action, date_range=expected)


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
    "date_update": {"action": "keep", "date_range": None},
    "time_update": {"action": "keep", "preference": None},
    "booking_answer": None,
}


@pytest.mark.parametrize(
    "preference",
    [
        {"kind": kind, "start_time": None, "end_time": None}
        for kind in ("morning", "afternoon", "evening", "any")
    ]
    + [
        {"kind": "window", "start_time": start, "end_time": end}
        for start, end in (
            ("14:00:00", "14:00:00"),
            ("14:00", "16:00"),
            ("15:00", None),
            (None, "11:00"),
        )
    ],
)
def test_time_preference_is_returned_from_the_validated_decision(preference):
    sdk = StubSDK(response=make_response(time_update={"action": "set", "preference": preference}))
    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "My time")])
    assert result.time_update == TimePreferenceUpdate(
        action="set", preference=TimePreference.model_validate(preference)
    )


@pytest.mark.parametrize("action", ["keep", "clear"])
def test_time_keep_and_clear_are_returned(action):
    sdk = StubSDK(response=make_response(time_update={"action": action, "preference": None}))
    result = make_client(sdk).generate(system="s", messages=[ChatMessage("user", "My time")])
    assert result.time_update == TimePreferenceUpdate(action=action, preference=None)


@pytest.mark.parametrize(
    "update",
    [
        None,
        {"action": "set", "preference": None},
        {"action": "invent", "preference": None},
        {"action": "clear", "preference": None, "extra": True},
    ]
    + [
        {"action": action, "preference": {"kind": "any", "start_time": None, "end_time": None}}
        for action in ("keep", "clear")
    ]
    + [
        {"action": "set", "preference": preference}
        for preference in (
            {"kind": "window", "start_time": None, "end_time": None},
            {"kind": "window", "start_time": "16:00", "end_time": "14:00"},
            {"kind": "window", "start_time": "23:00", "end_time": "01:00"},
            {"kind": "window", "start_time": "25:00", "end_time": None},
            {"kind": "window", "start_time": "tomorrow", "end_time": None},
            {"kind": "window", "start_time": 3600, "end_time": None},
            {"kind": "window", "start_time": True, "end_time": None},
            {"kind": "window", "start_time": "14:00:00Z", "end_time": None},
            {"kind": "window", "start_time": None, "end_time": "14:00:00-03:00"},
            {"kind": "window", "start_time": "2026-11-17T14:00:00", "end_time": None},
            {"kind": "morning", "start_time": "08:00", "end_time": None},
            {"kind": "any", "start_time": None, "end_time": "18:00"},
            {"kind": "night", "start_time": None, "end_time": None},
            {"kind": "morning", "start_time": None},
            {"kind": "any", "start_time": None, "end_time": None, "extra": True},
        )
    ],
)
def test_invalid_time_proposal_is_rejected_without_exposing_content(update):
    sdk = StubSDK(response=make_response(raw=json.dumps(_VALID_DECISION | {"time_update": update})))
    with pytest.raises(LLMError, match="invalid assistant decision") as error:
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])
    assert "patient secret" not in str(error.value)


def test_malformed_time_response_preserves_service_state_and_recovers_on_the_next_turn(
    escalation_service, clinic_config, fixed_clock
):
    sdk = StubSDK(
        response=make_response(
            intent="booking",
            time_update={
                "action": "set",
                "preference": {"kind": "window", "start_time": "14:00", "end_time": "16:00"},
            },
        )
    )
    service = ConversationService(
        make_client(sdk), "base", escalation_service, clinic_config, clock=fixed_clock
    )
    service.handle_message("one", "Entre 14 e 16")
    before = service._states["one"].model_copy(deep=True)
    sdk._response = make_response(
        intent="booking",
        time_update={
            "action": "set",
            "preference": {"kind": "window", "start_time": "14:00:00Z", "end_time": None},
        },
    )
    with pytest.raises(LLMError, match="invalid assistant decision"):
        service.handle_message("one", "Failed correction")
    assert service._states["one"] == before
    sdk._response = make_response(intent="booking")
    service.handle_message("one", "Continue")
    last_call = sdk.calls[-1]
    context = json.loads(last_call["system"].rsplit("\n", 1)[1])
    assert context["current_workflow_state"]["time_preference"] == {
        "kind": "window",
        "start_time": "14:00:00",
        "end_time": "16:00:00",
    }
    assert "Failed correction" not in [m["content"] for m in last_call["messages"]]


@pytest.mark.parametrize(
    "body",
    [
        "patient secret: not JSON",
        {"reply": "patient secret"},
        {k: v for k, v in _VALID_DECISION.items() if k != "booking_answer"},
        _VALID_DECISION | {"booking_answer": 123},
        {k: v for k, v in _VALID_DECISION.items() if k != "time_update"},
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
        {k: v for k, v in _VALID_DECISION.items() if k != "date_update"},
        _VALID_DECISION | {"date_update": None},
        _VALID_DECISION | {"date_update": {"action": "set", "date_range": None}},
        _VALID_DECISION | {"date_update": {"action": "invent", "date_range": None}},
        _VALID_DECISION
        | {
            "date_update": {
                "action": "clear",
                "date_range": {"start_date": "2026-11-18", "end_date": "2026-11-18"},
            }
        },
        _VALID_DECISION
        | {
            "date_update": {
                "action": "keep",
                "date_range": {"start_date": "2026-11-18", "end_date": "2026-11-18"},
            }
        },
        _VALID_DECISION
        | {
            "date_update": {
                "action": "set",
                "date_range": {"start_date": "2026-11-20", "end_date": "2026-11-18"},
            }
        },
        _VALID_DECISION
        | {
            "date_update": {
                "action": "set",
                "date_range": {"start_date": "2026-11-31", "end_date": "2026-11-31"},
            }
        },
        _VALID_DECISION
        | {
            "date_update": {
                "action": "set",
                "date_range": {"start_date": "tomorrow", "end_date": "tomorrow"},
            }
        },
        _VALID_DECISION
        | {
            "date_update": {
                "action": "set",
                "date_range": {"start_date": "2026-11-18T00:00:00", "end_date": "2026-11-18"},
            }
        },
        _VALID_DECISION
        | {
            "date_update": {
                "action": "set",
                "date_range": {"start_date": 1794956400, "end_date": "2026-11-18"},
            }
        },
        _VALID_DECISION
        | {"date_update": {"action": "set", "date_range": {"start_date": "2026-11-18"}}},
        _VALID_DECISION | {"date_update": {"action": "clear", "date_range": None, "extra": True}},
    ],
)
def test_invalid_decision_is_rejected_without_exposing_content(body):
    raw = body if isinstance(body, str) else json.dumps(body)
    sdk = StubSDK(response=make_response(raw=raw))
    with pytest.raises(LLMError, match="invalid assistant decision") as error:
        make_client(sdk).generate(system="s", messages=[ChatMessage("user", "Hi")])
    assert "patient secret" not in str(error.value)
