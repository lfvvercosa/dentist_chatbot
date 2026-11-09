"""The chat API contract. Every channel (Streamlit now, WhatsApp later) speaks this."""

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

# Chosen by the client (e.g. a UUID from Streamlit). Restricted characters keep
# ids safe to log and to use as storage keys.
ConversationId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]

# Whitespace is stripped first, so "   " counts as empty. The max length caps
# cost and abuse: every character ends up in an LLM prompt.
MessageText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class ChatRequest(BaseModel):
    conversation_id: ConversationId
    message: MessageText


class ChatResponse(BaseModel):
    conversation_id: ConversationId
    reply: str
