# Dental AI Assistant

A conversational assistant for a (fictional) dental clinic, built in four incremental versions in one repository. Each version is tagged (`v1.0` … `v4.0`) and runs on its own.

Planning documents live in [documents/](documents/). Per-version scope and decisions live in [docs/](docs/).

## Current status: V1, simple version working

A browser chat (Streamlit) that talks to a FastAPI backend, which asks Claude for each reply.
The assistant answers clinic questions only from [config/clinic.yaml](config/clinic.yaml),
collects appointment requests (it cannot book yet), and points urgent symptoms to professional
care. Claude returns an explicit escalation decision; the backend selects the appropriate
configured guidance. It replies in Portuguese or English, matching the patient.

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

## V1 behavior evaluations

Run the fictional patient scenarios in [evals/v1-cases.yaml](evals/v1-cases.yaml) against the
live model selected by `.env`:

```bash
uv run python -m scripts.eval_v1
```

This makes 13 paid API calls across 10 scenarios when all calls succeed. It runs the real
`ConversationService`, system prompt, and Claude adapter directly; no API or chat server is
needed. It is separate from the offline test suite.

Each run writes a new JSON report under `evals/results/`, including the fictional messages,
replies, criteria, rendered system prompt, model settings, returned models, tokens, and latency.
Use `--output path/to/new-report.json` to choose a path; existing reports are never overwritten.
Completed cases are saved as the run progresses.

**A completed API call is not a passing evaluation.** Read every conversation against both
the shared criteria and the case-specific criteria. Record pass/fail with a reason in a
companion review document, referencing the report and case id. API failures are execution
errors; dependent turns are skipped, and the command exits with a nonzero status. The JSON
review fields stay `pending` until someone explicitly grades them.

The [first baseline review](docs/v1-evaluation.md) records 9 passes and 1 brevity failure,
with no execution errors. Its report includes the reviewed verdicts and reasons. This is
one run of Sonnet at medium effort; other models and effort levels have not been compared.
The same review document also records a subsequent concision prompt change and rerun:
the booking case passed, while an unnecessary booking follow-up in the medication case
remains a brevity finding. Both runs and their original criteria are saved separately.
The user's [15:30 run](evals/results/20261111T183034467064Z.json) has also been reviewed:
9 passes and the same medication-reply brevity/focus finding, using the updated prompt.

## LLM-based escalation

Each message makes one Claude call returning `escalation` (`none`, `urgent`, or `emergency`),
`language` (`pt` or `en`), and a normal `reply`. Pydantic validates this internal response.
The public `/chat` contract is still just `conversation_id` and the final reply.

For `none`, the patient sees the generated reply. For `urgent` or `emergency`, the backend
uses [config/escalation.yaml](config/escalation.yaml), with emergency contacts from
`clinic.yaml`. This guidance is also the reply saved in conversation history. It tells the
patient where to seek help; nobody is automatically called or notified.

Recognition is performed by Claude using the current message and history; no keyword
matching or second classification call is used. Invalid decisions, refusal, or API failure
produce the existing HTTP 503 response. The feature depends on Claude being available and
classifying correctly. See [the design and results](docs/v1-escalation.md).

Run the separate contextual escalation scenarios (14 paid calls across 12 scenarios):

```bash
uv run python -m scripts.eval_v1 --cases evals/v1-escalation-cases.yaml
```

The report includes each structured `decision` and an `escalation_check` comparing it with
the expected level declared before the run. The command exits nonzero for a level mismatch
or execution error. These exact checks are separate from reviewing the full reply; its
review stays pending until recorded.
