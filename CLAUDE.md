# CLAUDE.md

Guidance for Claude when working in this repository.

## Project

Dental clinic AI assistant for a **fictional** clinic ("Big Smile Clinic"), built as four incremental versions in **one repo**, each tagged (`v1.0` … `v4.0`) and runnable on its own.

- **Read first:** `documents/01_PROJECT_CONTEXT.md`, `documents/02_FOUR_VERSION_ROADMAP.md`, `documents/03_ARCHITECTURE_AND_LLM_HANDOFF.md`.
- **Current release:** V1 is complete at `v1.0`; see `docs/v1-scope.md`. **Current development:** V2 steps 1.1–2.6 add typed in-memory state, booking intent, service/dentist/date/time extraction, configured compatibility checks, deterministic completeness assessment, and localized backend missing-detail questions. Dentist preference distinguishes unspecified, a specific dentist, and any eligible dentist. Dates use one inclusive range, with equal endpoints for a single day. Times support named periods, exact/inclusive windows, and explicit any; elapsed times are cleared when the selected date is only today. Step 3.1 adds a read-only CalendarService and in-memory mock; step 3.2 adds bounded slot calculation with AvailabilityService and explicit demo search settings. Both have offline demos. Step 3.3 connects mock availability to chat, saves exact options/check timestamps, and adds offering_slots with distinct search outcomes. Step 4.1 resolves selections from saved offers and adds exact summaries and slot_selected. Step 4.2 adds immutable booking proposals with UUID revisions, bound confirmation/refusal/abandonment, and awaiting_confirmation/ready_to_book. Calendar writes and Google access remain pending. See `docs/v2-scope.md` and inspect the code before proposing work; never assume a planned feature exists.
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
| `app/core/scheduling_config.py`, `config/scheduling.yaml` | Validated explicit demo search horizon, spacing, and result limit |
| `app/schemas/` | API contract (`ChatRequest`, `ChatResponse`) |
| `app/schemas/conversation.py` | V2 internal conversation history and workflow stage |
| `app/schemas/appointment.py` | V2 preference proposals/values, demo period bounds, and immutable AppointmentSlot candidates with exact intervals |
| `app/api/routes.py` | Thin routes; service injected with `Depends()` |
| `app/services/conversation.py` | Channel-independent entry point: capture clock, call LLM, run workflow, commit state/history, and log metadata |
| `app/services/scheduling.py` | SchedulingWorkflow routes escalation/FAQ/booking, chooses follow-up/search replies, and returns a proposed TurnOutcome |
| `app/services/preference_updates.py` | PreferenceResolver merges and validates proposals on a detached booking draft, returning completeness and clarification data |
| `app/services/preferences.py` | Pure completeness/compatibility assessment and localized missing-detail/conflict/expired-preference replies |
| `app/schemas/calendar.py` | Concrete TimeInterval values: nonempty aware timestamp ranges, normalized to UTC, with inclusive starts/exclusive ends |
| `app/services/calendar.py` | Read-only CalendarService contract, CalendarError, and MockCalendarService; explicit dentist registration, clipped/sorted busy reads, and simulated read failures |
| `app/services/availability.py` | AvailabilityService.find_slots: validated bounded search through injected CalendarService; clinic hours/duration, eligibility, future starts, time preferences, and busy overlap |
| `app/services/slot_offers.py` | Backend PT/EN demo options, exact selection summaries, invalid-choice and search/read outcomes |
| `app/services/slot_selection.py` | Resolve/retain exact saved choices, create fresh summary revisions, and render confirmation questions without calendar reads |
| `app/services/confirmation.py` | Bind approval to an unchanged pre-turn proposal, discard refused/abandoned drafts, and clear scheduling fields |
| `app/services/escalation.py` | Selects configured guidance from a validated LLM decision; never matches symptom keywords |
| `config/escalation.yaml` | Portuguese/English urgent and emergency templates, rendered with contacts from clinic config |
| `app/llm/client.py` | Provider-neutral `LLMClient` abstract base class, `ChatMessage`, `LLMResult`, `LLMError` |
| `app/llm/claude.py` | The only module that imports the `anthropic` SDK |
| `tests/` | Unit tests; must never call the network or need an API key |
| `scripts/` | Manual, opt-in scripts that may call the live API |

## Key decisions (don't silently change)

- **The LLM proposes, the backend executes.** Never report a booking or availability that code hasn't verified. Consequential actions require explicit patient confirmation enforced in code, not only in the prompt.
- ConversationService delegates decisions to SchedulingWorkflow, which uses PreferenceResolver and AvailabilityService. Workflow processing returns a proposed state/reply without editing accepted state/history; only ConversationService applies the successful outcome and appends the actual turn. Keep the public constructor and chat API stable when changing these internal boundaries.
- ConversationService requires an injected AvailabilityService. Complete valid non-escalating booking turns search unless resolving/retaining a saved selection; checked candidates enter offering_slots and save the captured request clock in UTC. Corrections, incomplete/conflicting choices, handled search/read errors, and escalation clear old offers; FAQs preserve the prior snapshot without rechecking; failed LLM decisions preserve all accepted state/history. Search/read errors are normal 200 dialogue replies that accept valid updates, whereas LLMError remains 503. Selection uses slot_update keep/set/clear and a strict positive one-based option_number. Python saves an exact selected_slot and a frozen booking_proposal (server UUID plus exact slot), asks for approval in awaiting_confirmation, and records bound approval as ready_to_book without a new calendar read or changed offer numbering/check time. FAQs preserve the selection; changed/cleared/expired preferences, handled search failures, and escalation clear it. Ambiguous/out-of-list choices clear selection but retain offers; missing/elapsed choices collect/search without selecting a replacement. No offer-age TTL is applied. Bare consent cannot select a default or authorize a write. ConfirmationUpdate keep/confirm/decline/abandon is required structured output. Confirm/decline reference the exact proposal UUID. A new selection always creates a new proposal and cannot be approved in the same turn. Approval requires pre-turn pending state, unchanged complete preferences, a future matching saved slot, matching ID, no selection correction, and no escalation; repeated matching approval is idempotent. Corrections invalidate pending and approved proposals. Refusal/abandonment clears the entire draft while retaining history; it never cancels an event. Whole abandonment can use other intent; FAQs with consent labels cannot approve. Stale references do not change current approval status. Booking writes remain pending. Do not add writes in this slice.
- API startup creates shared explicitly empty per-dentist mock calendars and injects AvailabilityService into ConversationService; the evaluation runner uses the same mode. `get_busy_intervals(dentist_id, window)` returns sorted/clipped [start,end) UTC intervals without merging. Empty means a successful read with no overlap; unknown/unmapped dentists and failed reads raise CalendarError. Mock seeds are copied validated TimeInterval objects; unavailable_dentists configures read failures for registered IDs. AvailabilityService applies clinic rules and future-start validation through that interface. Google and write methods are not implemented.
- Slot search uses config/scheduling.yaml (demo defaults: 30 clinic-local dates including today, 15-minute opening-anchored grid, three global results). Require specific/explicit-any dentist and wholly in-horizon future date preferences. Exact start times are checked without rounding; broad windows filter the grid; time bounds constrain starts while full service duration must fit clinic hours. Results sort by UTC start then dentist ID and remain unreserved alternatives. SlotSearchError covers invalid inputs/unsupported opening boundaries; CalendarError propagates and no partial offers escape. Use the caller's captured aware now, UTC elapsed durations, and local-time round trips for DST gaps/repetitions.
- Completeness is derived after validation on non-escalating booking turns. Require service, answered dentist (specific/any), date/range, and time/period/any; service/dentist conflicts also block completeness. Keep the result out of saved state. It drives conflict guidance, safe log field codes, and one localized missing-detail question in service/dentist/date/time order. Generic collection replaces the model's booking reply, preserving any separate `booking_answer` before the question; escalation/date/time/conflict guidance has priority, and any clear proposal preserves the model's specific clarification. Complete valid booking drafts get fresh backend-rendered mock offers or a saved-selection summary; standalone FAQs keep generated replies and the previous offer snapshot. `booking_answer` is a nullable clinic answer without follow-up questions from the same Claude call; it is preserved before collection/search outcomes and ignored on routes that keep another reply. The pure helper assumes configured-ID and date/time validation have already run; availability follows when complete.
- **Validate at the boundary** with Pydantic: YAML, HTTP bodies, and later LLM structured output.
- ClaudeClient sends `anthropic.transform_schema(AssistantDecision)` to the API because structured outputs support only a subset of JSON Schema. Keep the full Pydantic constraints for validating returned decisions; do not send the raw schema or weaken backend validation to satisfy provider limitations.
- **Server owns conversation history**, keyed by `conversation_id`; clients send only the id and the new message. History is stored as provider-neutral text (`ChatMessage`).
- Each LLM request includes a fresh JSON snapshot of saved `stage`, `service_id`, `dentist_preference`, `date_preference`, and `time_preference`, plus exact `candidate_slots`, `slots_checked_at`, `selected_slot`, `booking_proposal`, `current_time` in the configured clinic timezone, and explicit demo `time_periods`; message history stays separate. A timezone-aware clock is injectable for tests. Python clears past dates and fully elapsed times for a today-only date and supplies a clarification; ambiguous corrections use clear plus an LLM clarification. Time-only updates do not invent dates; date-only updates recheck retained times. Named periods are morning [00:00,12:00), afternoon [12:00,18:00), evening [18:00,end of day). These describe preferences, not opening hours. A configured but incompatible service/dentist pair remains unresolved and gets a backend-rendered clarification; never silently change either choice.
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
uv run python -m scripts.try_mock_calendar  # offline busy/empty/error calendar demo
uv run python -m scripts.try_availability   # offline exact-slot calculation demo
```

Python 3.12, managed with `uv` (add packages with `uv add`, dev packages with `uv add --dev`). Ruff line length is 100. Every step must end with tests and lint passing.

## Git

- Commit or push **only when the developer asks**. One commit per step, with a message saying what changed and why.
- **Never add AI attribution**: no `Co-Authored-By: Claude` trailer and no "Generated with Claude Code" line in commits or PRs.
- Never commit `.env` or secrets. The repo is public on GitHub.
- Tag each finished version (`v1.0`, …) and confirm it runs from a clean checkout.

## Updating docs

After each step, update `README.md` (run instructions) and the active version's scope (currently `docs/v2-scope.md`) when relevant. Preserve completed V1 records; label later planning revisions explicitly. When a version is done, record decisions and lessons in `docs/vN-decisions.md`.
