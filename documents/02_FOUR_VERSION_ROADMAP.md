# Dental AI Assistant — Four-Version Learning and Delivery Roadmap

## How to use this roadmap
Build releases sequentially in **one Git repository**. Each release is a **complete, independently runnable snapshot**, tagged `v1.0`, `v2.0`, `v3.0`, or `v4.0`. A user checking out any tag should be able to run that release using its own documented prerequisites, without depending on files or services introduced in a later version. Features are cumulative, but implementation may be refactored as complexity increases.

Avoid overengineering early releases. Each version should demonstrate a meaningful working patient journey and a distinct set of AI engineering concepts.

## V1 — Basic Dental AI Chatbot
**Learning objective:** Build a small but well-structured LLM application and understand frontend/backend separation, prompts, API contracts, validation, and basic conversation state.

**Interface:** Streamlit local browser chat, developed in VS Code. No WhatsApp.

**Backend:** FastAPI, LLM API client, typed/Pydantic request and response schemas, minimal conversation service. Keep a stable chat endpoint contract for future channels. Call the LLM provider's official SDK directly (no LangChain in V1), wrapped behind a small `LLMClient` interface so tests can use a fake.

**User-visible features:**
- Chat with a friendly dental receptionist in multiple turns.
- Ask about clinic services using a small, explicitly configured set of sample clinic facts.
- Explain symptoms or request an appointment; the bot asks sensible follow-up questions and remembers details within the running session.
- Recognize a scheduling intent and gather preferred service/date/time.
- Explain honestly that live availability and booking are **not yet supported**.
- Recognize potential dental urgency and direct the user to professional care; do not provide a diagnosis.
- Reset or start a fresh conversation for repeatable testing.

**Engineering boundaries:** Streamlit handles rendering and sending messages; FastAPI handles processing. Memory may be in-process/session-based and is not guaranteed across restarts. No LangGraph, PostgreSQL, Celery, Redis, Google Calendar, or WhatsApp required.

**Testing:** Basic API tests, schema validation tests, mocked LLM tests, and manual multi-turn acceptance scenarios. Never make ordinary unit tests depend on live LLM calls.

**Final V1 steps (added on top, only after the simple version works end to end):**
- **Behavior evals:** a small set of scenarios (grounded FAQ, unknown fact → "I don't know", red-flag symptom → escalation, booking request → "can't book yet"), run on demand against the live model. Separate from unit tests.
- **Deterministic red-flag check:** a code-level safety net for clear urgent symptoms that guarantees the escalation message, on top of the prompt-based behavior. Unit-testable.

**Done when:** The developer starts Streamlit and FastAPI locally, chats across multiple turns, observes relevant context retention, sees appropriate safety behavior, and can call the API directly using `/docs`.

## V2 — Agentic Scheduling with LangGraph
**Learning objective:** Learn stateful workflows, LangGraph graph design, tool calling, structured extraction, checkpoints, and safe external actions.

**Retain:** V1 chat interface, API, FAQ behavior, and safety boundaries.

**Add:**
- LangGraph workflow with explicit state, nodes, conditional routing, and a stable conversation/thread identifier.
- Structured extraction of intent, appointment type, preferred date/time, and missing details.
- Scheduling tools through a `CalendarService` abstraction, first with a mock implementation and then with a Google Calendar test calendar.
- Configured dentist-to-service matching, appointment durations, clinic hours, and timezone.
- Read availability, present real candidate slots, collect a slot selection, request explicit confirmation, recheck availability, and create the event.
- Clear error states for unavailable slots, invalid dates, failed calendar calls, and abandoned bookings.
- Development checkpointing (in-memory is acceptable for initial learning; persistent local checkpointing is an optional improvement). Do not imply in-memory state survives restart.
- Basic protection against duplicate booking attempts and repeated confirmations.

**Not required:** Multiple autonomous agents, PostgreSQL patient database, WhatsApp, Celery, Redis, AWS deployment.

**Testing:** Mock-calendar workflow tests for successful booking, missing information, no slots, confirmation refusal, stale slots, failed writes, and repeated requests. Separate opt-in integration test for a Google Calendar sandbox/test calendar.

**Done when:** From Streamlit, a patient requests a consultation, selects an actual available test-calendar slot, explicitly confirms, and sees a success message **only after** the event is created. Booking is not duplicated by a repeated confirmation.

## V3 — Durable Multi-Agent Application
**Learning objective:** Engineer persistence, specialized responsibilities, orchestration, state recovery, service/repository boundaries, and testable business workflows.

**Retain:** V2 working booking flow and Streamlit frontend.

**Add:**
- PostgreSQL for patient, appointment, and conversation business records.
- LangGraph PostgreSQL-backed checkpoints using stable thread identifiers; clearly distinguish graph checkpoints from authoritative appointment records.
- Database migrations and defined data access/repository interfaces.
- An orchestrator coordinating logical responsibilities: interaction/triage, scheduling, and communication/notification. Multi-agent orchestration is a committed V3 learning objective. Not every responsibility needs a separate LLM; deterministic functions are preferred where appropriate.
- Book, look up, reschedule, and cancel appointments, with authorization and explicit confirmation for modifications.
- Recover context and pending workflows after application restart.
- Better error recovery, structured logging, and observability of agent decisions.
- Expanded unit, integration, and workflow evaluation tests.
- Compare a simple workflow with the multi-agent design and document whether the added complexity is justified.

**Not required:** WhatsApp, distributed queues, cloud deployment. Streamlit remains the primary interface.

**Testing:** Restart-and-resume, appointment ownership verification, cross-conversation isolation, update/cancel consistency, partial calendar failures, and persistence integration tests.

**Done when:** A patient can return after a restart, recover their conversation/appointment context, book, reschedule, or cancel a verified appointment, and the system maintains consistent records with calendar state.

## V4 — WhatsApp and Production-Oriented Operations
**Learning objective:** Learn transport adapters, asynchronous processing, message reliability, deployment, operational security, and monitoring.

**Retain:** Streamlit as a development/testing interface and all V3 capabilities.

**Add:**
- WhatsApp Business Platform integration with webhook verification, incoming message parsing, outbound messaging, and approved templates when needed.
- Channel adapters that call the same `ConversationService` used by Streamlit.
- FastAPI webhook that acknowledges quickly; Redis and Celery workers for asynchronous processing and scheduled work.
- Message deduplication, idempotency, retries, ordering/serialization per conversation, and failure recovery.
- Appointment confirmations and optional reminders within channel/platform constraints.
- Docker/Compose for local reproducibility and an AWS deployment plan (ECS/Fargate is a candidate, not a mandatory choice).
- Secure configuration/secrets, authentication/authorization, monitoring, structured logs, alerting, backups, retention/deletion policies, and LGPD review.
- End-to-end tests from inbound WhatsApp event to confirmed booking, including failure and duplicate-delivery scenarios.

**Done when:** A patient can complete the supported appointment workflow via WhatsApp, the same core workflow remains usable through Streamlit, and duplicate webhooks or transient failures do not cause duplicate appointments. Deployment alone is not proof of production readiness; perform security and privacy validation before real patient use.

## Progressive capability matrix
| Capability | V1 | V2 | V3 | V4 |
|---|---|---|---|---|
| Streamlit chat | Yes | Yes | Yes | Retained |
| FastAPI backend | Yes | Yes | Yes | Yes |
| LLM + typed validation | Yes | Yes | Yes | Yes |
| Multi-turn memory | Ephemeral | Graph checkpoints | PostgreSQL durable | Durable |
| LangGraph | No | Yes | Yes | Yes |
| Google Calendar | No | Booking | Booking + changes | Same |
| Multi-agent orchestration | No | No | Yes | Yes |
| Patient/appointment database | No | Minimal/local if needed | PostgreSQL | PostgreSQL |
| WhatsApp | No | No | No | Yes |
| Redis/Celery | No | No | No | Yes |
| Cloud infrastructure | No | No | No | Yes |

## Release discipline
For **each** version:
1. Write a short scope and acceptance checklist before coding.
2. Implement the smallest vertical slice that can be demonstrated through Streamlit.
3. Add deterministic tests and a small manual test script.
4. Update the README with prerequisites, environment variables, run commands, demo scenarios, and known limitations.
5. Record architectural decisions and lessons learned in `docs/vN-*.md`.
6. Tag the working commit (`v1.0` etc.). Confirm it can be checked out and run independently.

## Explicitly deferred until justified
RAG/vector database, MCP, voice, sophisticated frontends, payments, complex clinic administration, and additional agent frameworks. Prefer a small reliable system over a broad fragile one.
