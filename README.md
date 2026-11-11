# Dental AI Assistant

A conversational assistant for a (fictional) dental clinic, built in four incremental versions in one repository. Each version is tagged (`v1.0` … `v4.0`) and runs on its own.

Planning documents live in [documents/](documents/). Per-version scope and decisions live in [docs/](docs/).

## Current release: V1 (`v1.0`)

A browser chat (Streamlit) that talks to a FastAPI backend, which asks Claude for each reply.
The assistant answers clinic questions only from [config/clinic.yaml](config/clinic.yaml),
collects appointment requests (it cannot book yet), and points urgent symptoms to professional
care. Claude returns an explicit escalation decision; the backend selects the appropriate
configured guidance. It replies in Portuguese or English, matching the patient.

```
Streamlit  →  FastAPI /chat  →  ConversationService  →  Claude
(display)     (HTTP only)       (history, logging)      (LLM)
```

See [docs/v1-scope.md](docs/v1-scope.md) for the completed checklist and
[docs/v1-decisions.md](docs/v1-decisions.md) for design choices, lessons, and limitations.

V2 will evolve this codebase on `main`. To run the fixed V1 snapshot in a repository
containing the release tag:

```bash
git switch --detach v1.0
```

Return to ongoing development with `git switch main`.

## Setup

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen         # install the exact dependencies recorded in uv.lock
cp .env.example .env     # then add your Anthropic API key
```

The key comes from the [Claude Console](https://platform.claude.com) (Settings → API Keys).
`.env` is git-ignored. Model and effort can be changed there too (see `.env.example`).

The code defaults to `claude-opus-5-5` with `low` effort. The saved live evaluations used
`claude-sonnet-5-5` with `medium` effort; to use that same configuration, add these lines
to `.env`:

```dotenv
LLM_MODEL=claude-sonnet-5-5
LLM_EFFORT=medium
```

No model comparison has been performed. Never copy the API key into committed files.

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
message), `503` if Claude is unavailable or returns an unusable decision. `GET /health`
reports that the local API is running; it does not check Claude availability.

## Development

```bash
uv run pytest            # run tests (offline: no network, no API key)
uv run ruff check .      # lint
uv run ruff format --check .  # verify formatting without changing files
```

The V1 release has 64 offline tests. To apply formatting, use `uv run ruff format .`.

To try a single Claude call without the API server (needs credentials; incurs API usage):

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

[Historical reviews](docs/v1-evaluation.md) preserve the original replies, prompt versions,
and recorded judgments, including subjective brevity findings. These are small learning
experiments, not a model ranking. The later escalation work has separate, explicit checks
and final evidence below; earlier reports were not rewritten to reflect later fixes.

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

The [final escalation report](evals/results/v1-llm-escalation-final-sonnet-medium.json)
matched all 14 expected decisions across 12 scenarios in one Sonnet/medium run, with no
execution errors. It includes negation, resolved past symptoms, unresolved conversation
context, English/Portuguese replies, and instructions attempting to suppress escalation.

## Known limitations

- No calendar access or actual booking, rescheduling, or cancellation. The assistant gathers
  preferences and directs the patient to call.
- History belongs to one API process and disappears on restart. It has no length cap or
  expiration. "New conversation" changes the id; it does not delete the previous server history.
- Escalation recognition depends on Claude and may be incorrect. The fixed templates are
  learning examples, not clinically validated policies. Nobody is automatically notified.
- Authentication, rate limiting, durable storage, and patient-data retention/deletion workflows
  are outside V1. Use fictional data for this local learning demo.
- Clinic details and phone numbers are fictional. Model comparisons, repeated-run reliability
  measurements, and remaining style refinements are follow-up work.
