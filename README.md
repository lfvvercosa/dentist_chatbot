# Dental AI Assistant

A conversational assistant for a (fictional) dental clinic, built in four incremental versions in one repository. Each version is tagged (`v1.0` … `v4.0`) and runs on its own.

Planning documents live in [documents/](documents/). Per-version scope and decisions live in [docs/](docs/).

## Current status: V1, simple version working

A browser chat (Streamlit) that talks to a FastAPI backend, which asks Claude for each reply.
The assistant answers clinic questions only from [config/clinic.yaml](config/clinic.yaml),
collects appointment requests (it cannot book yet), and points urgent symptoms to professional
care. It replies in Portuguese or English, matching the patient.

```
Streamlit  →  FastAPI /chat  →  ConversationService  →  Claude
(display)     (HTTP only)       (history, logging)      (LLM)
```

See [docs/v1-scope.md](docs/v1-scope.md) for scope, steps and the acceptance checklist.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # create .venv and install dependencies
cp .env.example .env     # then add your Anthropic API key
```

The key comes from the [Claude Console](https://platform.claude.com) (Settings → API Keys).
`.env` is git-ignored. Model and effort can be changed there too (see `.env.example`).

## Run the chat

Two terminals, from the repo root.

**1. API** (the log lines for each message appear here):

```bash
uv run uvicorn app.main:app --reload
```

**2. Chat page:**

```bash
uv run streamlit run frontend/streamlit_app.py
```

Open http://localhost:8501 and chat. Things to try:

- "Vocês abrem no sábado?" (answered from the config)
- "How much is a cleaning?" (no prices in the config: says it doesn't know)
- "Quero marcar uma limpeza" (collects service, date and time; says to call the clinic)
- "Meu dente dói e o rosto inchou" (directs to the emergency phone, no diagnosis)

"New conversation" in the sidebar starts a fresh chat (a new `conversation_id`). The page only
displays messages and calls `POST /chat`; the history lives on the server, in memory, so it is
lost when the API restarts (with `--reload`, on every saved `.py` file). If the API runs
elsewhere, set `CHAT_API_URL` (default `http://localhost:8000`).

## Call the API directly

With the API running, open http://localhost:8000/docs (Swagger), or:

```bash
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"conversation_id": "demo-1", "message": "Vocês abrem no sábado?"}'
```

The client sends only the conversation id and the new message. Reusing the same id continues
the conversation. Responses: `200` with the reply, `422` for an invalid body (e.g. a blank
message), `503` if Claude is unavailable.

## Development

```bash
uv run pytest            # run tests (offline: no network, no API key)
uv run ruff check .      # lint
uv run ruff format .     # format
```

To try a single Claude call without the API (needs the key; costs a fraction of a cent):

```bash
uv run python -m scripts.try_claude
```
