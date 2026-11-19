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

V2 development has started; see [docs/v2-scope.md](docs/v2-scope.md). Steps 1.1–2.6 introduce typed,
in-memory conversation state, booking intent, service/dentist/date/time preference extraction,
deterministic preference completeness checks, and backend missing-detail questions.
Each Claude request includes saved preferences and the current time in the configured clinic
timezone, alongside the separate message history.
A booking turn collects missing preferences or moves to `offering_slots` after a successful
search. FAQs preserve the pending draft/options/selection, while escalation takes priority and clears
saved preferences, options, and selection. Claude proposes keeping, setting, or clearing a
service or dentist, or explicitly accepting any eligible dentist. Python validates the IDs and
checks the merged service/dentist pair against clinic configuration. An incompatible pair gets
a clarification with eligible dentists; the requested choices remain until the patient changes
them. An unanswered preference remains distinct from "any dentist."

Dates use keep/set/clear updates and one inclusive start/end range; a single day has equal
endpoints. Claude interprets relative dates from the supplied clock and is instructed to ask
about ambiguous dates. Python rejects past dates, clears the date preference, and asks for a
replacement without changing service/dentist choices. Today is allowed as a date preference.

Time preferences support morning/afternoon/evening, exact times, inclusive windows (including
one-sided bounds), and an explicit "any time," distinct from unanswered. Named periods have
explicit demo meanings: morning before noon, afternoon from noon to 18:00, and evening from
18:00 to the end of the day. Times describe appointment starts in the configured clinic timezone.
Claude proposes keep/set/clear updates; Python validates their shape and clears fully elapsed
times when the selected date is only today. Partially elapsed windows and preferences applying
to future days are retained. Date corrections also recheck the retained time. Ambiguous
corrections are instructed to clear the old time and ask for clarification.

After merging and validating preferences on a booking turn, Python reports missing fields in
service/dentist/date/time order and any service/dentist conflict. Explicit any dentist/any time
count as answers; named periods and date ranges also count. A draft is complete only when no
fields are missing and no compatibility conflict remains. This derived result is not saved as
a second state flag. It drives the existing dentist-conflict clarification and appears in logs
as `preferences_complete`, `missing_preferences`, and `preference_conflicts`. FAQ/escalation
turns skip this assessment and log `None` for those fields.

For ordinary incomplete booking turns, the backend asks one missing-detail question in that
same order, in Portuguese or English. The dentist question lists configured eligible providers
and offers the explicit any choice; it never automatically selects the only eligible dentist.
The actual question is saved in history and evaluation reports. Escalation, past-date/time,
and dentist-conflict guidance take priority. A clear proposal preserves Claude's specific
ambiguity/withdrawal clarification; complete valid drafts now trigger backend availability
searches. Standalone FAQs keep their normal generated answers. For mixed clinic-question/
booking messages, Claude's internal `booking_answer` carries the clinic answer without a
follow-up question; Python preserves it before adding a missing-detail question or search outcome. The same
Claude call supplies both, and the public API still returns just conversation_id and reply.

Step 3.1 adds a read-only CalendarService contract and an in-memory mock with fictional busy
intervals. Step 3.2 adds AvailabilityService, which calculates exact candidate appointments
using clinic hours, service duration, preferences, and mock busy intervals. The standalone
demos below exercise these services. Step 3.3 connects them to chat: API startup creates one
shared mock calendar with an explicitly empty calendar for each configured dentist, then
injects AvailabilityService into ConversationService. Complete non-escalating booking turns
perform a fresh search and return checked, clearly labeled demonstration options, except when
resolving or retaining a saved selection as described below.

The server saves exact candidate slots and their request-clock timestamp as `candidate_slots`
and `slots_checked_at`. Changed preferences replace or clear old offers; no matches, invalid search
inputs, or calendar read errors clear offers and receive distinct backend replies. An out-of-range
date request gets the actual allowed dates. FAQs retain the previous offer snapshot without
rechecking it. A failed model call/decision preserves accepted state/history. Demo offers show
full dates, times, UTC offsets, service/dentist names, and clinic timezone. Nothing is reserved.

Step 4.1 adds selection and an exact summary. After offers, Claude proposes a `slot_update`:
`set` with a one-based `option_number`, `keep`, or `clear` for an ambiguous/withdrawn selection.
Python resolves the choice against the saved list without searching again or changing its order,
saves the exact `selected_slot`, and enters `slot_selected`. The localized summary shows the
stored service, dentist, start/end timestamps with UTC offsets, and clinic timezone. Selecting
from an any-dentist list preserves that preference. A selection is neither confirmation nor
a reservation; the summary states that availability may have changed and booking is unavailable.

FAQs preserve a selection. Changed/cleared/expired preferences and escalation clear it. An
ambiguous choice clears the selection and retains offers for clarification; an out-of-list
number receives backend guidance. A missing offer list or elapsed chosen start causes collection
or fresh offers without selecting from the replacement list. Bare consent never selects an
option by default; a retained valid selection receives its summary again. Failed model calls or
decisions preserve accepted state/history. Evaluation reports include the proposal and subsequent
selected-slot snapshot. Offline tests use scripted decisions; live interpretation is not evaluated.

The Python flow is split by responsibility:

```text
ConversationService   → call the model, run the workflow, save state/history, log metadata
  SchedulingWorkflow  → route escalation/FAQ/booking, ask questions, search availability
    PreferenceResolver → merge proposals into a separate draft and validate preferences
    resolve_slot_selection → resolve or retain an exact saved choice and render its summary
    AvailabilityService → calculate slots through CalendarService
```

The workflow returns a proposed state and reply. ConversationService applies them only after
processing succeeds, so an invalid proposal cannot leave a partially changed booking draft.

V2 will add confirmation/refusal/abandonment handling, availability rechecking, verified booking, and
duplicate protection, followed by Google test calendars.
V3 will first migrate the working flow
to LangGraph, then add PostgreSQL persistence, then multi-agent orchestration. WhatsApp and
deployment infrastructure remain V4 work.

When testing a booking request, the backend log now includes
`intent=booking stage=collecting_preferences`. This records the workflow stage, not a confirmed
appointment. A successful offer uses `stage=offering_slots` with `availability=found` and the
new-offer count. Other search outcomes are `none_found`, `invalid_search`, and `read_error`;
unsearched turns log `not_searched`. Normal logs do not include patient messages. Intent interpretation still depends
on Claude; offline tests verify transitions with fakes rather than real model judgments.

Development continues in this codebase on `main`. To run the fixed V1 snapshot in a repository
containing the release tag:

```bash
git switch --detach v1.0
```

Return to ongoing development with `git switch main`.

## Offline mock calendar demo

```bash
uv run python -m scripts.try_mock_calendar
```

This requires no API key, server, or network. It registers a mock calendar for each configured
dentist and seeds the first dentist with fictional busy intervals on the fixed demo date
2026-11-19. It prints occupied periods 09:00–10:00 and 11:00–12:00 in the configured clinic
timezone, an empty result for 12:00–13:00, and a simulated read failure.

`CalendarService.get_busy_intervals(dentist_id, window)` returns sorted busy intervals clipped
to a concrete TimeInterval. Intervals require aware timestamps, normalize to UTC, and include
their start while excluding their end. A busy interval ending at 10:00 does not overlap one
starting at 10:00. Returned intervals may overlap each other; they are not merged. Each known
empty mock calendar is explicitly registered with an empty list; an unmapped dentist or failed
read raises CalendarError instead of returning an empty result.

The mock copies its seed data and performs no writes. An empty result establishes only that
the mock has no busy intervals in that query window; clinic hours, service duration, future
times, and slot spacing are applied by the slot calculator described below. Chat,
Google integration, confirmation, and booking are not exercised by this demo.

## Offline slot calculation demo

```bash
uv run python -m scripts.try_availability
```

This also requires no credentials, server, or network. It uses the first configured service
and its first eligible dentist, a fixed clock/date, and a fictional busy period 08:30–10:00.
With the current clinic configuration, it prints these 30-minute consultation alternatives:

```text
2026-11-19 08:00-08:30
2026-11-19 10:00-10:30
2026-11-19 10:15-10:45
```

The explicit demo policy in [config/scheduling.yaml](config/scheduling.yaml) searches the next
30 clinic-local dates including today, uses a 15-minute grid anchored at each day's opening,
and returns at most three options across all eligible dentists. Options are sorted by start
instant and then dentist ID. Alternative options may overlap each other; none is reserved.
An exact requested start is checked as given, including an off-grid time. A broad time window
filters grid starts without rounding or changing the patient's bounds. Named periods and time
bounds constrain appointment starts; the full configured service duration must fit clinic hours.

Only strictly future starts without busy overlap are returned. Closed days are skipped.
An explicit any dentist searches only providers configured for that service. Past or partly
out-of-horizon date ranges raise SlotSearchError without silent trimming; no matching slots
returns [], while a failed required calendar read raises CalendarError without partial offers.
Time calculations use UTC instants and the configured clinic timezone for local preferences.
The returned AppointmentSlot values carry service ID, dentist ID, and an exact TimeInterval.
Chat uses the same calculator, then resolves selections from saved options. Confirmation and
calendar writes remain later work.

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
- "Quero marcar uma limpeza" (collects missing service/dentist/date/time preferences)
- "Quero uma consulta amanhã de manhã com a Dra. Ana" (shows checked demo options)
- "A segunda opção" (after offers: stores that exact choice and shows its summary)
- "Meu dente dói e o rosto inchou" (directs to the emergency phone, no diagnosis)

Restart the API to load the new startup wiring. Demo calendars start empty, so these results
are fictional; the standalone demos use their own seeded busy intervals. You can correct
preferences and get fresh offers, or select a saved option. Confirmation and booking are not
implemented; replying "book option 1" selects it and shows its summary without reserving or
creating anything. A subsequent "yes" only repeats the selected summary in this slice.

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
Handled search validation, no-slot, and mock-calendar read failures return `200` with an honest
chat reply, preserve the accepted preferences, and clear old offers. They remain distinct from
an unusable model decision, which returns `503` without changing accepted state/history.

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
Runs on the current development code use the mock-backed V2 chat flow. New reports declare
mock mode, initially empty per-dentist calendars, and the demo scheduling policy; historical
reports remain unchanged. Use the v1.0 checkout described above to reproduce released V1 behavior.

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

For `none`, the patient sees a generated answer or a backend preference/availability reply.
For `urgent` or `emergency`, the backend
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

- Chat reads an in-memory demonstration calendar only; no Google/real clinic calendar is
  connected. Confirmation, actual booking, rescheduling, and cancellation are unavailable.
  Offers and selected slots are unreserved snapshots. Selection retains the saved check time;
  the calendar must be checked again before a future booking write. There is no offer-age TTL.
- History belongs to one API process and disappears on restart. It has no length cap or
  expiration. "New conversation" changes the id; it does not delete the previous server history.
- Escalation recognition depends on Claude and may be incorrect. The fixed templates are
  learning examples, not clinically validated policies. Nobody is automatically notified.
- Authentication, rate limiting, durable storage, and patient-data retention/deletion workflows
  are outside V1. Use fictional data for this local learning demo.
- Clinic details and phone numbers are fictional. Model comparisons, repeated-run reliability
  measurements, and remaining style refinements are follow-up work.
