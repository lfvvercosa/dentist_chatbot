import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_conversation_service
from app.llm.client import ChatMessage, LLMError
from app.main import app
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
def client(fake_llm, escalation_service, clinic_config, fixed_clock):
    # One service for the whole test, so history survives across requests.
    service = ConversationService(
        fake_llm, "test system prompt", escalation_service, clinic_config, clock=fixed_clock
    )
    app.dependency_overrides[get_conversation_service] = lambda: service
    # Not used as `with TestClient(...)`, so the startup code (which builds the
    # real Claude client) never runs.
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("intent", ["other", "booking"])
def test_chat_returns_reply_in_the_same_public_contract(client, fake_llm, intent):
    fake_llm.intent = intent
    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "  hello  "})

    assert response.status_code == 200
    expected = "Qual serviço você deseja agendar?" if intent == "booking" else "reply 1"
    assert response.json() == {"conversation_id": "abc-123", "reply": expected}
    _, messages = fake_llm.calls[0]
    assert messages == [ChatMessage("user", "hello")]  # stripped before reaching the LLM


def test_mixed_booking_answer_and_backend_question_share_the_same_chat_contract(client, fake_llm):
    fake_llm.intent = "booking"
    fake_llm.language = "en"
    fake_llm.booking_answer = "We open at 08:00 on Mondays."
    response = client.post(
        "/chat",
        json={"conversation_id": "abc-123", "message": "Can I book? When do you open Monday?"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": "abc-123",
        "reply": "We open at 08:00 on Mondays.\n\nWhich service would you like to schedule?",
    }


def test_chat_returns_503_when_llm_fails(client, fake_llm):
    fake_llm.error = LLMError("Could not reach Claude API: timeout")

    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "hello"})

    assert response.status_code == 503
    assert response.json() == {
        "detail": "The assistant is temporarily unavailable. Please try again."
    }  # internal error details are not leaked to the client


def test_unknown_service_proposal_returns_503_without_exposing_the_id(client, fake_llm):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="unknown")

    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "Book"})

    assert response.status_code == 503
    assert "unknown" not in response.text


def test_unknown_dentist_proposal_returns_503_without_exposing_the_id(client, fake_llm):
    fake_llm.intent = "booking"
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="private_unknown")

    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "Book"})

    assert response.status_code == 503
    assert "private_unknown" not in response.text


def test_incompatible_pair_is_a_clarification_in_the_same_chat_contract(client, fake_llm):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="bruno")

    response = client.post(
        "/chat", json={"conversation_id": "abc-123", "message": "Limpeza com Bruno"}
    )

    assert response.status_code == 200
    assert set(response.json()) == {"conversation_id", "reply"}
    assert "Dr. Ana Souza" in response.json()["reply"]


def test_chat_returns_escalation_text_in_the_same_public_contract(
    client, fake_llm, escalation_service
):
    fake_llm.escalation = "emergency"
    fake_llm.language = "en"

    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "Help"})

    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": "abc-123",
        "reply": escalation_service.reply("emergency", "en"),
    }


def test_past_date_returns_a_clarification_in_the_same_public_contract(client, fake_llm):
    fake_llm.intent = "booking"
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-16", end_date="2026-11-16")
    )

    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "Ontem"})

    assert response.status_code == 200
    assert set(response.json()) == {"conversation_id", "reply"}
    assert "a partir de 2026-11-17" in response.json()["reply"]


def test_elapsed_time_returns_a_clarification_in_the_same_public_contract(client, fake_llm):
    fake_llm.intent = "booking"
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-17", end_date="2026-11-17")
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="window", start_time="08:00", end_time="08:00")
    )
    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "Hoje às 8h"})
    assert response.status_code == 200
    assert set(response.json()) == {"conversation_id", "reply"}
    assert "já passou hoje" in response.json()["reply"]


@pytest.mark.parametrize(
    "payload",
    [
        {"conversation_id": "abc-123"},  # missing message
        {"conversation_id": "abc-123", "message": "   "},  # blank after stripping
        {"conversation_id": "abc-123", "message": "x" * 2001},  # too long
        {"conversation_id": "bad id!", "message": "hello"},  # unsafe characters
        {"message": "hello"},  # missing conversation_id
    ],
)
def test_chat_rejects_invalid_requests(client, fake_llm, payload):
    response = client.post("/chat", json=payload)

    assert response.status_code == 422
    assert fake_llm.calls == []  # invalid input never costs an LLM call
