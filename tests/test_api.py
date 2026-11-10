import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_conversation_service
from app.llm.client import ChatMessage
from app.main import app
from app.services.conversation import ConversationService


@pytest.fixture
def client(fake_llm):
    # One service for the whole test, so history survives across requests.
    service = ConversationService(fake_llm, system_prompt="test system prompt")
    app.dependency_overrides[get_conversation_service] = lambda: service
    # Not used as `with TestClient(...)`, so the startup code (which builds the
    # real Claude client) never runs.
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_llm_reply(client, fake_llm):
    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "  hello  "})

    assert response.status_code == 200
    assert response.json() == {"conversation_id": "abc-123", "reply": "reply 1"}
    _, messages = fake_llm.calls[0]
    assert messages == [ChatMessage("user", "hello")]  # stripped before reaching the LLM


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
