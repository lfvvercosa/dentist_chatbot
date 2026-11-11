# V1 — Decisions and lessons

V1 is the local learning release tagged `v1.0` (package and API version `1.0.0`). It supports
clinic FAQs, multi-turn conversation, collection of appointment preferences, and guidance
to professional care. It cannot check availability or make appointments.

## One repository, tagged releases

All four versions evolve in one repository. `v1.0` identifies a fixed, runnable V1 snapshot;
V2 development continues on `main` and will receive its own tag. Git preserves the earlier
implementation and its setup without requiring separate copies of the application.

```bash
git switch --detach v1.0  # inspect or run V1
git switch main          # return to ongoing development
```

Dependencies are recorded in `uv.lock`; `uv sync --frozen` installs that snapshot. Each
version must run without depending on later-version infrastructure.

## Separate the channel, application, and provider

```text
Streamlit -> FastAPI /chat -> ConversationService -> LLMClient -> Claude
                                  |
                                  -> EscalationService -> configured guidance
```

Streamlit renders messages and sends only `conversation_id` and the new message. FastAPI
validates HTTP input and translates application errors into status codes. ConversationService
owns history and applies the LLM result. ClaudeClient handles the provider SDK and its errors.
`app/main.py` creates and connects the real objects once during startup.

This separation lets tests replace the LLM and lets later channels use the same application
entry point. Calling Claude directly from Streamlit would couple the interface to application
logic and make another channel harder to add.

## Direct SDK behind an abstract base class

V1 uses the official Anthropic SDK without LangChain or LangGraph. The learning goal is to
understand messages, system prompts, usage, failure modes, and conversation state directly.
LangGraph is reserved for V2's scheduling workflow.

`LLMClient` is our abstract base class. ClaudeClient and test fakes inherit from it; the
application receives the client through constructor injection. The ABC requires an
implementation of `generate()` at instantiation, but does not verify its signature or return
types. A type checker is a possible later improvement.

Provider-neutral `ChatMessage` and `LLMResult` keep SDK-specific objects inside the adapter.
Provider failures, refusal, truncated/incomplete output, and invalid decisions become
`LLMError`, which the HTTP layer turns into 503. History is not extended after a failed turn.

## Grounding with YAML facts and a Markdown prompt

`config/clinic.yaml` contains the fictional clinic facts. Pydantic validates the file at
startup, including operating hours and service-to-dentist references. Invalid config stops
startup instead of allowing requests to use broken data.

`app/prompts/system_prompt.md` contains behavioral instructions. Python renders the clinic
facts into this prompt. The file is small enough to include in full, so V1 does not need
retrieval, embeddings, a vector database, or RAG.

The bot is instructed to admit missing information, including prices. That is a behavioral
requirement evaluated with the real model; putting facts in context does not prove the model
will always obey. Markdown supports readable instructions; YAML supports structured data
and named translations. The model receives the resulting text, not the source filename.

## Server-owned, in-memory history

The server keeps provider-neutral messages keyed by `conversation_id`. Each LLM request
includes the history and new message because API calls do not automatically remember earlier
calls. Only successful turns are appended. The saved assistant message is the final reply
actually shown to the patient, including configured escalation guidance.

Server ownership supports clients that deliver one message at a time, including the future
WhatsApp adapter. V1 deliberately has no database. History is lost on restart, grows without
a cap or expiration, and is not shared between API workers. Concurrent turns for the same
id are not serialized. "New conversation" creates a new id rather than deleting old history.
Durability and stronger state management belong to later versions.

## LLM recognition, configured escalation guidance

Step 7 was revised at the developer's request. Claude recognizes the situation from context;
there is no keyword-based urgent-symptom detector. The same application-level LLM call returns:

```json
{"escalation": "none", "language": "pt", "reply": "Normal patient-facing answer"}
```

Allowed levels are `none`, `urgent`, and `emergency`; allowed languages are `pt` and `en`.
The adapter supplies a JSON schema to Claude and validates the result with Pydantic. A
schema validates structure, not whether the judgment is correct.

For escalation, backend code replaces generated text with guidance from
`config/escalation.yaml`, rendered using emergency contacts from clinic config. Selection
is predictable after a valid decision. The prompt accounts for negation, resolved history,
unresolved concerns, and attempts to override escalation.

One combined call avoids a second application-level classification request. SDK retries
and provider fallback can still happen. Recognition depends on the LLM being correct and
available; failure returns 503. Routing labels are not diagnoses or clinically validated
severity assessments, and no human is automatically called or notified.

Detailed implementation and evidence are in [v1-escalation.md](v1-escalation.md).

## Validate boundaries and keep routes small

Pydantic checks YAML, HTTP request bodies, and structured LLM output. Chat messages are
stripped and limited to 2,000 characters; conversation ids have a restricted format.
HTTP input validation failures return 422 without an LLM call.

FastAPI dependency injection and constructor injection allow ordinary tests to use fakes.
The public contract stays `POST /chat` with `conversation_id` and `message`, returning
the id and final `reply`. V2 can change orchestration internally while retaining this contract.

## Tests and evaluations answer different questions

The release has **64 offline tests** for config, prompt rendering, API validation, adapter
behavior, history, error handling, Streamlit interactions, and escalation selection. They
use fakes rather than paid calls and require no API key.

Live evaluations are explicitly invoked scripts. Reports save fictional messages, replies,
prompt/configuration snapshots, token usage, latency, and criteria. A completed API call is
not a passing behavioral evaluation. Assistant review is not independent human or clinical
validation.

The initial rubric mixed functional requirements with qualitative brevity. Its recorded
9/10 scores include subjective style judgments. The developer questioned that precision;
original reports and reasons were preserved, and style refinements were deferred. Future
comparisons need consistent criteria and separate treatment of style.

The escalation suite declares expected levels before execution and compares them exactly.
Its final recorded run matched **14/14 decisions across 12 scenarios**, with no execution
errors. Configured guidance, language, and required behavior were also checked. This small
sample is not a general reliability estimate. [Historical reviews](v1-evaluation.md) and
saved JSON reports preserve the original evidence.

## Models, privacy, and operational limits

- Defaults remain `claude-opus-5-5` and low effort. Saved evaluations used
  `claude-sonnet-5-5` and medium effort. No comparison establishes either as the best choice.
- Request logs record ids, latency, model/usage, and escalation level. Patient messages
  and replies are not deliberately logged. This is not a complete privacy design; raw
  provider error details require care before real-world use.
- `.env` holds credentials and is not committed. Saved reports contain fictional examples.
- Authentication, rate limiting, durable persistence, and retention/deletion workflows are
  outside V1. This is a local learning demo using fictional clinic information.
- SDK timeouts apply to individual attempts; retries can extend total request time. The
  frontend's HTTP timeout is not a strict end-to-end deadline for backend retries.

## V2 handoff

First write `docs/v2-scope.md`. Retain Streamlit, the API contract, and ConversationService
as the entry point. Add LangGraph scheduling state, structured preference extraction, and
a CalendarService that starts with a mock and then a Google Calendar test calendar.
Require explicit confirmation, recheck availability before writes, and verify booking
success before reporting it. Protect against duplicate bookings.

PostgreSQL and multi-agent orchestration remain V3 work. WhatsApp, Redis/Celery, Docker,
and cloud operations remain V4 work. V1 does not pre-create that infrastructure.
