import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)  # calls the app in-process; no server or network needed


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_reply_for_same_conversation():
    response = client.post("/chat", json={"conversation_id": "abc-123", "message": "  hello  "})

    assert response.status_code == 200
    assert response.json() == {"conversation_id": "abc-123", "reply": "You said: hello"}


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
def test_chat_rejects_invalid_requests(payload):
    response = client.post("/chat", json=payload)

    assert response.status_code == 422
