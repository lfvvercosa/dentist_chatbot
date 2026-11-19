"""Internal conversation state, separate from the public chat API schemas."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.core.clinic_config import Slug
from app.llm.client import ChatMessage
from app.schemas.appointment import (
    AppointmentSlot,
    BookingProposal,
    DatePreference,
    DentistPreference,
    TimePreference,
)

ConversationStage = Literal[
    "chatting", "collecting_preferences", "offering_slots", "awaiting_confirmation", "ready_to_book"
]


class ConversationState(BaseModel):
    """State for one conversation, stored in memory by ConversationService."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(default_factory=list)
    stage: ConversationStage = "chatting"
    service_id: Slug | None = None
    dentist_preference: DentistPreference = Field(default_factory=DentistPreference)
    date_preference: DatePreference | None = None
    time_preference: TimePreference | None = None
    candidate_slots: list[AppointmentSlot] = Field(default_factory=list)
    slots_checked_at: AwareDatetime | None = None
    selected_slot: AppointmentSlot | None = None
    booking_proposal: BookingProposal | None = None
