"""Internal conversation state, separate from the public chat API schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.clinic_config import Slug
from app.llm.client import ChatMessage
from app.schemas.appointment import DatePreference, DentistPreference

ConversationStage = Literal["chatting", "collecting_preferences"]


class ConversationState(BaseModel):
    """State for one conversation, stored in memory by ConversationService."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(default_factory=list)
    stage: ConversationStage = "chatting"
    service_id: Slug | None = None
    dentist_preference: DentistPreference = Field(default_factory=DentistPreference)
    date_preference: DatePreference | None = None
