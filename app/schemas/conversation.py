"""Internal conversation state, separate from the public chat API schemas."""

from pydantic import BaseModel, ConfigDict, Field

from app.llm.client import ChatMessage


class ConversationState(BaseModel):
    """State for one conversation, stored in memory by ConversationService."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(default_factory=list)
