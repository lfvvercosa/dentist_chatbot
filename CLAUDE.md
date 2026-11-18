# CLAUDE.md

Guidance for Claude when working in this repository.

## Project

Dental clinic AI assistant for a **fictional** clinic ("Big Smile Clinic"), built as four incremental versions in **one repo**, each tagged (`v1.0` … `v4.0`) and runnable on its own.

- **Read first:** `documents/01_PROJECT_CONTEXT.md`, `documents/02_FOUR_VERSION_ROADMAP.md`, `documents/03_ARCHITECTURE_AND_LLM_HANDOFF.md`.
- **Current release:** V1 is complete at `v1.0`; see `docs/v1-scope.md`. **Current development:** V2 steps 1.1–2.6 add typed in-memory state, booking intent, service/dentist/date/time extraction, configured compatibility checks, deterministic completeness assessment, and localized backend missing-detail questions. Dentist preference distinguishes unspecified, a specific dentist, and any eligible dentist. Dates use one inclusive range, with equal endpoints for a single day. Times support named periods, exact/inclusive windows, and explicit any; elapsed times are cleared when the selected date is only today. Calendar access and booking are not implemented yet. See `docs/v2-scope.md` and inspect the code before proposing work; never assume a planned feature exists.
- V2 uses explicit typed conversation state and ordinary Python scheduling functions. LangGraph, PostgreSQL, and multi-agent orchestration are V3; introduce them in that order. WhatsApp, Redis, Celery, Docker and AWS are V4.

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
| `app/schemas/conversation.py` | V2 internal conversation history and workflow stage |
| `app/schemas/appointment.py` | V2 service/dentist/date/time proposals, preference values, and explicit demo period bounds; ConversationService checks configured IDs, compatibility, past dates, and elapsed times today |
| `app/api/routes.py` | Thin routes; service injected with `Depends()` |
| `app/services/` | `ConversationService`: channel-independent entry point for a message |
| `app/services/preferences.py` | Pure completeness/compatibility assessment and localized first-missing-field questions over validated merged preferences |
| `app/services/escalation.py` | Selects configured guidance from a validated LLM decision; never matches symptom keywords |
| `config/escalation.yaml` | Portuguese/English urgent and emergency templates, rendered with contacts from clinic config |
| `app/llm/client.py` | Provider-neutral `LLMClient` abstract base class, `ChatMessage`, `LLMResult`, `LLMError` |
| `app/llm/claude.py` | The only module that imports the `anthropic` SDK |
| `tests/` | Unit tests; must never call the network or need an API key |
| `scripts/` | Manual, opt-in scripts that may call the live API |

## Key decisions (don't silently change)

- **The LLM proposes, the backend executes.** Never report a booking or availability that code hasn't verified. Consequential actions require explicit patient confirmation enforced in code, not only in the prompt.
- Completeness is derived after validation on non-escalating booking turns. Require service, answered dentist (specific/any), date/range, and time/period/any; service/dentist conflicts also block completeness. Keep the result out of saved state. It drives conflict guidance, safe log field codes, and one localized missing-detail question in service/dentist/date/time order. Generic collection replaces the model's booking reply, preserving any separate `booking_answer` before the question; escalation/date/time/conflict guidance has priority, and any clear proposal preserves the model's specific clarification. Complete drafts and standalone FAQs keep generated replies. `booking_answer` is a nullable clinic answer without follow-up questions from the same Claude call; it is ignored on routes that keep another reply. The pure helper assumes configured-ID and date/time validation have already run; availability is a later step.
- **Validate at the boundary** with Pydantic: YAML, HTTP bodies, and later LLM structured output.
- **Server owns conversation history**, keyed by `conversation_id`; clients send only the id and the new message. History is stored as provider-neutral text (`ChatMessage`).
- Each LLM request includes a fresh JSON snapshot of saved `stage`, `service_id`, `dentist_preference`, `date_preference`, and `time_preference`, plus `current_time` in the configured clinic timezone and explicit demo `time_periods`; message history stays separate. A timezone-aware clock is injectable for tests. Python clears past dates and fully elapsed times for a today-only date and supplies a clarification; ambiguous corrections use clear plus an LLM clarification. Time-only updates do not invent dates; date-only updates recheck retained times. Named periods are morning [00:00,12:00), afternoon [12:00,18:00), evening [18:00,end of day). These describe preferences, not opening hours. A configured but incompatible service/dentist pair remains unresolved and gets a backend-rendered clarification; never silently change either choice.
- **Clinic facts go into the system prompt** (context stuffing). No RAG or vector DB.
- **No LangChain in V1.** Use the official `anthropic` SDK behind `LLMClient`.
- **No LangGraph in V2.** Implement the scheduling flow in Python first. At the start of V3, migrate it to LangGraph while preserving behavior, then add PostgreSQL persistence, then multi-agent orchestration. Booking safeguards remain backend rules in both implementations.
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

After each step, update `README.md` (run instructions) and the active version's scope (currently `docs/v2-scope.md`) when relevant. Preserve completed V1 records; label later planning revisions explicitly. When a version is done, record decisions and lessons in `docs/vN-decisions.md`.
