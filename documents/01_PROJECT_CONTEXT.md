# Dental AI Assistant — Project Context and Product Requirements

## Purpose of this document
This document is the durable product brief for an LLM coding assistant working on the Dental AI Assistant project. Read it before proposing architecture, implementation, or changes. It captures the product vision and decisions made in planning conversations. It is a specification of intended behavior, **not** evidence that functionality has already been implemented.

## Project vision
Build a conversational AI assistant for a dental clinic, initially as a local web chat and later as a WhatsApp assistant. It should help patients understand the clinic's services, communicate their needs, identify an appropriate service or dentist, discover appointment availability, book appointments after explicit confirmation, and eventually reschedule and cancel appointments. It must recognize when a human professional should take over, particularly for potentially urgent dental symptoms.

The developer is a data scientist learning practical AI engineering. The project is simultaneously a useful application and a deliberate, incremental learning exercise. Explain design tradeoffs and introduce complexity only when a feature requires it. Favor understandable, testable components over impressive but unnecessary infrastructure.

## Non-negotiable decisions
- **One repository** with four incrementally developed, runnable releases. Do not create four disconnected projects or duplicate entire codebases.
- **Each version must work independently** when checked out at its Git tag, with its own documented setup, configuration, and demonstration path. Later versions must not be required to run earlier versions.
- **Streamlit is the first chat interface**, developed locally in VS Code. Do not require WhatsApp in V1–V3.
- **FastAPI is the backend API**; Streamlit is a thin client and should not own business logic.
- **WhatsApp is deferred to V4** as another adapter to the same application capabilities. Streamlit should remain available as a developer/testing interface.
- Build and test locally first. Introduce deployment infrastructure only when needed.
- Use Python, typed data contracts, and clear boundaries between chat transport, orchestration, business services, persistence, and external integrations.
- Introduce LangGraph when the application has a real stateful workflow (V2), not simply to wrap a basic V1 chat.
- Start with simple conversation history; introduce durable state and multi-agent coordination progressively.
- Use mock integrations before connecting real external systems.
- Do not confuse an LLM's proposed action with a completed action. Calendar and messaging operations must be executed and verified by backend services.

## Target users and channels
**Initial user:** the developer, playing the role of a patient in Streamlit to explore workflows and debug behavior.

**Later users:** actual patients interacting via WhatsApp, and potentially clinic staff managing exceptions and appointments. A staff dashboard is not in the committed scope.

**Channel progression:** Streamlit (V1–V3) → Streamlit + WhatsApp (V4). Chat channels must map to a common conversation service and stable patient/conversation identifiers.

## Functional capabilities, progressively enabled
1. Natural-language patient interaction, with follow-up questions and context across turns.
2. Answer clinic-specific FAQs **only from configured, trustworthy clinic information**; do not invent prices, dentists, procedures, policies, hours, or availability.
3. Recognize requests for a consultation, cleaning, tooth pain, and other dental services; collect relevant scheduling preferences.
4. Provide bounded, non-diagnostic triage: recognize possible urgency and escalate to a dentist or emergency service when appropriate. The chatbot is not a clinical diagnostician.
5. Match a requested service to an appropriate dentist based on explicitly configured service-to-provider rules.
6. Retrieve real availability from Google Calendar once integration is enabled.
7. Propose available slots, require explicit confirmation, recheck availability, and create an appointment.
8. Persist and later retrieve patient, conversation, and appointment data.
9. Reschedule and cancel existing appointments with authorization and proper calendar synchronization.
10. Send confirmations and, eventually, reminders/notifications via the enabled channel.
11. Recover from failures, retries, duplicate messages, and interrupted conversations without duplicate bookings.

## Example patient journeys
### FAQ / first contact
Patient: “What types of appointments do you offer?”
Assistant: Answers using clinic configuration, or says it does not have verified information.

### Initial symptom / appointment request
Patient: “My tooth hurts. Can I come tomorrow afternoon?”
Assistant: Acknowledges, asks only relevant follow-up questions, flags urgent warning signs when present, identifies the appropriate appointment type, and collects date/time preferences. In V1 it clearly states it cannot check availability. From V2 onward it can check a calendar and offer real slots.

### Confirmed booking (V2+)
Patient selects an offered slot → assistant summarizes dentist/service/date/time → patient explicitly confirms → backend rechecks slot and creates appointment → assistant reports success only after a verified result.

### Change appointment (V3+)
Patient asks to move an existing booking → system identifies the correct appointment and verifies authorization → checks alternatives → confirms intended change → updates the calendar and records → sends accurate confirmation.

## Safety, trust, and privacy requirements
- The assistant must not diagnose, prescribe, or replace dental assessment. For red-flag symptoms (e.g., significant facial swelling, breathing/swallowing difficulty, severe bleeding), direct the patient toward appropriate urgent professional care.
- Do not invent availability, successful bookings, provider specialties, or clinic policies.
- Request only the minimum necessary patient information. Treat health-related messages as sensitive personal data under Brazil's LGPD; avoid storing unnecessary clinical detail, limit access, and define retention/deletion practices before production use.
- Obtain explicit confirmation before booking, modifying, or canceling appointments. For changes to existing appointments, require appropriate patient verification.
- Use timezone-aware appointment times, with clinic timezone defined in configuration.
- Handle API failures honestly: say when availability or booking cannot be confirmed, and provide a fallback.
- Design for human handoff when uncertain, out of scope, or potentially urgent.
- All clinic-specific facts, calendar IDs, operating hours, dentist specialties, duration rules, and message templates must be configuration/data, not hallucinated defaults.

## Product scope boundaries
Not initially required: voice, images, medical record integration, payments, insurance claims, full electronic health records, vector database/RAG, MCP servers, autonomous clinical decision-making, or a clinic staff dashboard. These can be considered later only with clear user requirements.

## Definition of a successful project
A newcomer can clone the repo, follow the README for a specific tagged version, run the chat, complete that version's demonstrated workflows, and understand which new AI engineering capability that version introduces. By V4, the same booking backend supports both Streamlit and WhatsApp without duplicating core business logic.
