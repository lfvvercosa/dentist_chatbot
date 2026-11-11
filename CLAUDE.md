# CLAUDE.md

Guidance for Claude when working in this repository.

## Project

Dental clinic AI assistant for a **fictional** clinic ("Big Smile Clinic"), built as four incremental versions in **one repo**, each tagged (`v1.0` … `v4.0`) and runnable on its own.

- **Read first:** `documents/01_PROJECT_CONTEXT.md`, `documents/02_FOUR_VERSION_ROADMAP.md`, `documents/03_ARCHITECTURE_AND_LLM_HANDOFF.md`.
- **Current version and progress:** `docs/v1-scope.md` (decisions, numbered steps, acceptance checklist). Check it and inspect the code before proposing work; never assume a planned feature exists.
- Do not introduce infrastructure reserved for a later version (LangGraph is V2; PostgreSQL and multi-agent are V3; WhatsApp, Redis, Celery, Docker and AWS are V4).

## How to work with the developer

The developer is a data scientist learning AI engineering and preparing for interviews. They must be able to explain every design choice.

- **One small step at a time.** Implement a single step, verify it, explain it, then stop for review. Never batch several steps.
- **Minimal code, senior practices.** No speculative abstractions, no empty folders.
- **Explain the why.** For each step: what was built, how a request or data flows through it, key design choices and the main alternative. Use short code excerpts and real outputs (run the code; don't guess output).
- When a trade-off matters, recommend one option and say why; don't list every alternative.
- Extras (evals, safety nets, hardening) go as separate steps **after** the simple version works.

## Architecture (V1)

```
Streamlit → FastAPI route → ConversationService → LLMClient → Claude
(display)   (HTTP only)    (app logic, no HTTP)   (adapter)
```

| Path | Responsibility |
|---|---|
| `config/clinic.yaml` | All clinic facts (hours, dentists, services, policies). The bot may only state facts from here |
| `app/core/clinic_config.py` | Pydantic models + `load_clinic_config()`; validates the YAML at load (fail fast) |
| `app/core/settings.py` | Settings from env vars / `.env` (`pydantic-settings`) |
| `app/schemas/` | API contract (`ChatRequest`, `ChatResponse`) |
| `app/api/routes.py` | Thin routes; service injected with `Depends()` |
| `app/services/` | `ConversationService`: channel-independent entry point for a message |
| `app/services/escalation.py` | Selects configured guidance from a validated LLM decision; never matches symptom keywords |
| `config/escalation.yaml` | Portuguese/English urgent and emergency templates, rendered with contacts from clinic config |
| `app/llm/client.py` | Provider-neutral `LLMClient` abstract base class, `ChatMessage`, `LLMResult`, `LLMError` |
| `app/llm/claude.py` | The only module that imports the `anthropic` SDK |
| `tests/` | Unit tests; must never call the network or need an API key |
| `scripts/` | Manual, opt-in scripts that may call the live API |

## Key decisions (don't silently change)

- **The LLM proposes, the backend executes.** Never report a booking or availability that code hasn't verified. Consequential actions require explicit patient confirmation enforced in code, not only in the prompt.
- **Validate at the boundary** with Pydantic: YAML, HTTP bodies, and later LLM structured output.
- **Server owns conversation history**, keyed by `conversation_id`; clients send only the id and the new message. History is stored as provider-neutral text (`ChatMessage`).
- **Clinic facts go into the system prompt** (context stuffing). No RAG or vector DB.
- **No LangChain in V1.** Use the official `anthropic` SDK behind `LLMClient`.
- **Claude settings** are configurable via `.env`: `LLM_MODEL` (default `claude-opus-5-5`), `LLM_EFFORT` (default `low`), timeout 60s, refusal fallback (`fallbacks="default"`) enabled. Check `stop_reason` before reading content; translate SDK errors to `LLMError`.
- The bot replies in the patient's language (Portuguese or English). It is an administrative assistant: no diagnosis; urgent symptoms are directed to professional care.
- Escalation recognition uses the LLM's contextual decision in the same call as the reply. Validate the structured level/language/reply at the adapter boundary; backend code selects the template. The developer rejected a keyword detector. Recognition is not available independently of the LLM.
- Patient data is sensitive (Brazil's LGPD): never log message text or personal data.

## Commands

```bash
uv sync                                  # install dependencies
uv run pytest                            # all tests (offline)
uv run ruff check . && uv run ruff format --check .
uv run uvicorn app.main:app --reload     # API; Swagger at http://localhost:8000/docs
uv run python -m scripts.try_claude      # one live Claude call (needs ANTHROPIC_API_KEY in .env)
```

Python 3.12, managed with `uv` (add packages with `uv add`, dev packages with `uv add --dev`). Ruff line length is 100. Every step must end with tests and lint passing.

## Git

- Commit or push **only when the developer asks**. One commit per step, with a message saying what changed and why.
- **Never add AI attribution**: no `Co-Authored-By: Claude` trailer and no "Generated with Claude Code" line in commits or PRs.
- Never commit `.env` or secrets. The repo is public on GitHub.
- Tag each finished version (`v1.0`, …) and confirm it runs from a clean checkout.

## Updating docs

After each step, update `README.md` (run instructions) and `docs/v1-scope.md` (step status) when relevant. When a version is done, record decisions and lessons in `docs/vN-decisions.md`.
