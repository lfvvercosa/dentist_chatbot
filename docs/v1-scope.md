# V1 — Scope and Acceptance Checklist

## Goal
A small, well-structured LLM chatbot: Streamlit chat → FastAPI → ConversationService → Claude. It answers clinic questions only from configured facts, gathers what the patient wants to book, points urgent symptoms to professional care, and says honestly that it cannot book yet.

## Decisions
| Topic | Decision | Main alternative considered |
|---|---|---|
| LLM | Claude, via the official `anthropic` SDK, behind a small `LLMClient` interface so the provider can be swapped | LangChain (hides the raw API we want to learn) |
| Language | Bot replies in the patient's language: Portuguese or English | Single language |
| Conversation history | Stored on the server, keyed by `conversation_id`; clients send only the id + new message. In-memory in V1 (lost on restart) | Client sends full history each request (stateless, but WhatsApp can't do this) |
| Clinic facts | YAML file validated by Pydantic, rendered into the system prompt | RAG (not needed for ~1 page of facts) |
| System prompt | Lives in its own file, versioned with the code | Inline string |
| Logging | One structured log line per chat request: request id, latency, token usage. No patient message text | No logging until V3 |
| Tooling | Python 3.12, uv, pytest, ruff | pip/poetry, mypy (maybe later) |

## Out of scope for V1
LangGraph, booking or calendar access, databases, WhatsApp, Redis/Celery, Docker, deployment.

## Steps
0. Project setup (git, pyproject, tooling, this document)
1. Clinic config (YAML) + Pydantic model + validation tests
2. API contract: `ChatRequest`/`ChatResponse`, FastAPI `/health` and `/chat` with an echo reply, API tests
3. `LLMClient` (Claude + fake), system prompt file, `ConversationService` with in-memory history, request logging
4. Streamlit thin client with "new conversation"
5. Manual acceptance run + README run instructions (simple version complete)
6. Behavior evals (on demand, live model)
7. Deterministic red-flag safety check
8. `docs/v1-decisions.md`, final README, tag `v1.0`

## Acceptance checklist
- [ ] FastAPI and Streamlit start locally with documented commands
- [ ] `/docs` shows the chat endpoint and it works when called directly
- [ ] Multi-turn chat keeps context within a conversation
- [ ] "New conversation" starts fresh; two conversations don't mix
- [ ] Clinic questions are answered from config; unknown facts (e.g. prices) get "I don't have that information"
- [ ] Booking requests: bot collects service/date/time and says it cannot book yet
- [ ] Red-flag symptoms get directed to urgent professional care, with no diagnosis
- [ ] Replies in Portuguese or English, matching the patient
- [ ] Unit tests pass without network or API key
