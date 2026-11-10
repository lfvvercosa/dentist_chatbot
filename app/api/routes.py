"""HTTP layer: validate input, call the service, shape the output. No business logic here."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.llm.client import LLMError
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.conversation import ConversationService

router = APIRouter()


def get_conversation_service(request: Request) -> ConversationService:
    """Returns the service built at startup (app/main.py). Tests swap it via
    app.dependency_overrides."""
    return request.app.state.conversation_service


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/chat")
def chat(
    request: ChatRequest,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ChatResponse:
    try:
        reply = service.handle_message(request.conversation_id, request.message)
    except LLMError as exc:
        # Our problem, not the client's: 503 says "try again later". The details are
        # already logged by the service and are not sent to the client.
        raise HTTPException(
            status_code=503, detail="The assistant is temporarily unavailable. Please try again."
        ) from exc
    return ChatResponse(conversation_id=request.conversation_id, reply=reply)
