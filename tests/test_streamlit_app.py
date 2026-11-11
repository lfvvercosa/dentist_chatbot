"""Chat page tests. AppTest runs the Streamlit script in memory (no browser, no server);
httpx2.post is replaced by a fake, so no request leaves the process."""

from pathlib import Path

import httpx2
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).parents[1] / "frontend" / "streamlit_app.py")


class FakeBackend:
    """Stands in for POST /chat: records each JSON body and answers "reply 1", "reply 2", ...
    Set `status` to make it answer with an HTTP error instead."""

    def __init__(self):
        self.bodies: list[dict] = []
        self.status = 200

    def post(self, url: str, json: dict, timeout: float) -> httpx2.Response:
        self.bodies.append(json)
        request = httpx2.Request("POST", url)
        if self.status != 200:
            return httpx2.Response(self.status, request=request)
        return httpx2.Response(200, json={"reply": f"reply {len(self.bodies)}"}, request=request)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    fake = FakeBackend()
    monkeypatch.setattr(httpx2, "post", fake.post)
    return fake


def send(at: AppTest, text: str) -> AppTest:
    return at.chat_input[0].set_value(text).run()


def test_sends_only_conversation_id_and_new_message(backend):
    at = AppTest.from_file(APP_PATH).run()
    send(at, "Hi")
    send(at, "Are you open on Saturday?")

    conversation_id = at.session_state.conversation_id
    # Only the id and the new message: the server keeps the history.
    assert backend.bodies == [
        {"conversation_id": conversation_id, "message": "Hi"},
        {"conversation_id": conversation_id, "message": "Are you open on Saturday?"},
    ]
    assert [m.markdown[0].value for m in at.chat_message] == [
        "Hi",
        "reply 1",
        "Are you open on Saturday?",
        "reply 2",
    ]


def test_new_conversation_uses_a_new_id_and_clears_the_chat(backend):
    at = AppTest.from_file(APP_PATH).run()
    send(at, "Hi")
    first_id = at.session_state.conversation_id

    at.sidebar.button[0].click().run()
    assert len(at.chat_message) == 0

    send(at, "Hello again")
    assert backend.bodies[1]["conversation_id"] != first_id


def test_failed_reply_shows_an_error_and_is_not_kept(backend):
    backend.status = 503
    at = AppTest.from_file(APP_PATH).run()
    send(at, "Hi")

    assert "HTTP 503" in at.error[0].value
    assert at.session_state.messages == []
