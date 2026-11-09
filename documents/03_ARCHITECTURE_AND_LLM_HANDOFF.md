# Dental AI Assistant — Architecture Principles and LLM Handoff Guide

## Instructions to a future coding LLM
You are collaborating on a learning-oriented, incremental Python AI engineering project. Read `01_PROJECT_CONTEXT.md` and `02_FOUR_VERSION_ROADMAP.md` first. **Ask which version is currently being developed and inspect the repository before making changes.** Do not assume that a planned feature is already implemented. The user prefers discussing implementation specifics interactively; this handoff intentionally avoids prescribing exact code.

Your role is to help make each version simple, functional, understandable, and testable. Explain why a tool or architectural pattern is appropriate at that stage. Offer incremental steps and validate them before expanding scope. Do not silently introduce infrastructure reserved for a later version.

## Shared architectural direction
The eventual message flow is:

`Streamlit or WhatsApp → FastAPI/channel adapter → ConversationService → LangGraph orchestration → business services/tools → calendar + persistence → reply through originating channel`

In V1 the middle can be a simple conversation service with an LLM call; LangGraph is introduced in V2. In V4 WhatsApp messages are handled asynchronously through webhook, queue, and worker. The chat frontend and WhatsApp transport must not directly implement scheduling business rules.

### Responsibilities
- **Streamlit:** Display messages, collect input, manage UI state and conversation identifier, call FastAPI. Do not call the LLM or Google Calendar directly.
- **FastAPI:** Validate API requests, expose chat endpoints, route requests to application services; later receive WhatsApp webhooks.
- **ConversationService:** The application entry point for a message. Load conversation context, invoke the appropriate workflow, and return a response. Preserve a stable contract as channels evolve.
- **LLM interaction layer:** Prompting, structured extraction, language generation, and tool-selection proposals. The LLM is not the authoritative record of bookings.
- **LangGraph (V2+):** Maintain workflow state, transitions, confirmation checkpoints, and agent routing as complexity grows.
- **Interaction/triage responsibility (V3+):** Understand intent, collect relevant details, handle FAQ, identify urgent concerns and out-of-scope messages.
- **Scheduling responsibility:** Identify suitable dentist/service, retrieve available slots, request confirmation, and call deterministic booking/change/cancel services.
- **Communication/notification responsibility:** Compose clear user-facing messages and notifications; channel adapters perform delivery.
- **CalendarService:** Encapsulate availability and event operations, validate inputs, normalize timezones, and translate external API failures. Provide mock and Google Calendar implementations.
- **Repositories/persistence (V3+):** Authoritative patient, appointment, and conversation records. Separate these from LangGraph checkpoints.
- **Background worker (V4):** Process webhook events and notifications with retries and deduplication.

## Decisions and design tradeoffs from earlier discussions
1. **Modular monolith first:** Organize code into cohesive packages rather than deploying multiple microservices prematurely.
2. **Agents are not equivalent to services:** A deterministic calendar API wrapper or notification sender does not need an LLM. Multi-agent coordination should solve a concrete routing/reasoning problem, not merely increase agent count.
3. **Typed boundaries:** Validate incoming/outgoing API data and tool arguments; use structured outputs when appropriate. Never trust free-form model text as a database command.
4. **Separation of channels:** Streamlit comes first; WhatsApp is a later input/output adapter, not a reason to rewrite core scheduling logic.
5. **Persistence evolves:** V1 can use ephemeral history; V2 introduces LangGraph checkpoints; V3 uses PostgreSQL for durable checkpoints and business records. A checkpoint is not a substitute for an appointment database.
6. **Async only when needed:** V1–V3 can use straightforward request/response processing for local learning. Redis/Celery are introduced with real webhook reliability requirements in V4.
7. **Mock before real integration:** Exercise calendar behavior with a fake service before requesting OAuth credentials or changing real calendars.
8. **No invented clinical or operational facts:** Services, provider mappings, clinic hours, prices, availability, and policies must come from configured data.
9. **Human-in-the-loop for consequential actions:** Require explicit user confirmation for bookings, rescheduling, and cancellations; verify authorization for existing appointments.
10. **Tests at every stage:** Do not postpone testing until V3. Isolate nondeterministic LLM behavior and live external APIs from most tests.

## Representative state model (conceptual, not a fixed schema)
- Conversation/thread identifier and patient identifier (when known)
- Recent messages and/or concise conversation summary
- Current intent (FAQ, booking, rescheduling, cancellation, handoff)
- Symptoms or requested procedure, with data minimization
- Requested service and candidate dentist
- Preferred date/time and clinic timezone
- Available slots and selected slot
- Confirmation status and current workflow step
- Appointment identifier and verified operation result
- Error, retry, or handoff status

Do not rely on an LLM to infer all of this repeatedly from raw conversation history. Keep important transactional fields explicit. Use stable identifiers to avoid mixing patients or conversations.

## Calendar workflow invariants
- Never claim a slot is available without checking the calendar.
- Never claim a booking succeeded before receiving verified success from the calendar service.
- Never create/change/cancel without clear patient confirmation and the necessary authorization.
- Recheck availability immediately before booking; handle races and conflicts.
- Make repeat confirmations and retries idempotent, including across process failures in later versions.
- Treat timezones, appointment duration, operating hours, dentist assignment, and calendar ownership as explicit configuration.
- Persist sufficient identifiers to reconcile internal appointment records with external calendar events.
- When a tool fails, disclose the uncertainty and offer a safe retry or human handoff rather than fabricating a result.

## Clinic-specific safety and privacy
The chatbot is an administrative assistant, not a dentist. It may ask limited clarifying questions and direct patients toward appropriate professional evaluation, but should not diagnose, prescribe, or confidently classify medical severity. Recognize urgent warning signs and escalate. Before handling real patients, establish LGPD-compatible lawful basis, access controls, minimization, retention, deletion, logging redaction, and security review. Avoid exposing patient information in traces, prompts, and logs beyond what is necessary.

## Recommended repo organization (evolve rather than pre-create everything)
- `app/api/` — HTTP endpoints and channel adapters
- `app/services/` — conversation and business services
- `app/agents/` — LLM-based responsibilities introduced when needed
- `app/workflows/` — LangGraph state and graph definition
- `app/schemas/` — typed contracts
- `app/repositories/` — persistence adapters
- `app/core/` — configuration and shared infrastructure
- `frontend/` — Streamlit UI
- `tests/` — unit, integration, workflow, and eventual E2E tests
- `docs/` — per-release decisions, setup, and learning notes
- `README.md`, `pyproject.toml`, `.env.example`, and eventually Docker assets

The folder names are illustrative. Prefer coherent boundaries over a rigid template, and do not create empty folders just to match the eventual architecture.

## Collaboration protocol for the LLM
When beginning work or a new milestone:
1. Confirm the targeted release (V1–V4) and summarize the smallest working feature to build next.
2. Inspect the current code, tests, and configuration before suggesting changes.
3. State the design decision and one or two meaningful alternatives when a tradeoff matters.
4. Propose a small vertical slice with an observable demo and acceptance criteria.
5. Implement or guide implementation only after the scope is understood; keep changes localized.
6. Verify with tests and a manual conversation scenario.
7. Update docs and release checklist; avoid claiming success without verification.

## Suggested acceptance conversations
- V1: Patient asks about clinic services, then asks for an appointment; the assistant remembers context and explicitly says it cannot book yet.
- V2: Patient requests cleaning, selects a real test-calendar slot, confirms, and receives a verified booking; repeated confirmation does not duplicate it.
- V3: Patient returns after restart, identifies an existing appointment, requests rescheduling, verifies identity, confirms, and receives a consistent calendar/database update.
- V4: A WhatsApp webhook is retried; only one booking occurs, and the patient receives a single coherent confirmation. Streamlit still works.

## Open decisions to resolve during implementation
- LLM provider/model and budget constraints.
- Sample clinic information, dentist roster, services, operating hours, appointment durations, and timezone.
- How to identify/verify patients, especially for appointment modifications.
- Google Calendar authorization strategy and test calendars.
- Exact thread ID and patient ID mapping.
- Where and how to persist message history in each version.
- Whether the V3 multi-agent design demonstrably improves on the simpler V2 workflow.
- Hosting provider and WhatsApp onboarding details for V4.

## Final reminder
The goal is **four teachable, independently executable milestones in a single evolving repository**, not four parallel products. Prioritize Streamlit first, real scheduling second, durable orchestration third, and WhatsApp/production operations fourth. Maintain a usable application at each tagged release.
