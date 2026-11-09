"""HTTP layer: validate input, call the service, shape the output. No business logic here."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.chat import ChatRequest, ChatResponse
from app.services.conversation import ConversationService

router = APIRouter()

_conversation_service = ConversationService()


def get_conversation_service() -> ConversationService:
    """Dependency provider. Tests can swap the service via app.dependency_overrides."""
    return _conversation_service


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/chat")
def chat(
    request: ChatRequest,
    service: Annotated[ConversationService, Depends(get_conversation_service)],
) -> ChatResponse:
    reply = service.handle_message(request.conversation_id, request.message)
    return ChatResponse(conversation_id=request.conversation_id, reply=reply)
