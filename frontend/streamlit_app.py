"""Chat page: a thin client of the FastAPI backend. Run with:
uv run streamlit run frontend/streamlit_app.py

It only displays messages and calls POST /chat. The server keeps the conversation history,
so each request carries just the conversation id and the new message. It never imports `app`:
like WhatsApp later, it talks to the backend through the HTTP contract only.
"""

import os
import uuid

import httpx2
import streamlit as st

API_URL = os.environ.get("CHAT_API_URL", "http://localhost:8000")
# Longer than the server's LLM timeout (60s), so the server gives up first and
# answers with a clean 503 instead of this page cutting the request.
TIMEOUT_SECONDS = 70
MAX_MESSAGE_CHARS = 2000  # same limit as ChatRequest; avoids a confusing 422


def start_new_conversation() -> None:
    st.session_state.conversation_id = uuid.uuid4().hex
    # Display copy only. The history Claude sees lives on the server.
    st.session_state.messages = []


def ask_backend(conversation_id: str, message: str) -> str:
    response = httpx2.post(
        f"{API_URL}/chat",
        json={"conversation_id": conversation_id, "message": message},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["reply"]


# Streamlit reruns this whole script on every interaction; session_state survives reruns.
st.set_page_config(page_title="Big Smile Clinic", page_icon="🦷")
st.title("Big Smile Clinic assistant")

if "conversation_id" not in st.session_state:
    start_new_conversation()

with st.sidebar:
    st.button("New conversation", on_click=start_new_conversation)
    st.caption(f"Conversation id: `{st.session_state.conversation_id}`")

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if text := st.chat_input("Type your message", max_chars=MAX_MESSAGE_CHARS):
    with st.chat_message("user"):
        st.markdown(text)
    try:
        with st.spinner("Thinking..."):
            reply = ask_backend(st.session_state.conversation_id, text)
    except httpx2.RequestError:
        st.error(f"Could not reach the API at {API_URL}. Is it running?")
    except httpx2.HTTPStatusError as exc:
        st.error(f"The assistant could not answer (HTTP {exc.response.status_code}). Try again.")
    else:
        # Saved only on success, like the server: a failed question is not kept.
        st.session_state.messages += [
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply},
        ]
        with st.chat_message("assistant"):
            st.markdown(reply)
