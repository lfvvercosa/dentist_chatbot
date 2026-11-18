"""Validated LLM output, separate from the public HTTP response contract."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.client import ConversationIntent, EscalationLevel, ResponseLanguage
from app.schemas.appointment import (
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreferenceUpdate,
)


class AssistantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    escalation: EscalationLevel = Field(description="Escalation needed in the current context")
    language: ResponseLanguage = Field(description="Patient's language; Portuguese if unclear")
    intent: ConversationIntent = Field(
        description="booking for a new appointment request or its follow-up; other otherwise"
    )
    service_update: ServicePreferenceUpdate = Field(
        description="Change to the requested service in the current patient message"
    )
    dentist_update: DentistPreferenceUpdate = Field(
        description="Change to the dentist preference in the current patient message"
    )
    date_update: DatePreferenceUpdate = Field(
        description="Change to the date preference in the current patient message"
    )
    time_update: TimePreferenceUpdate = Field(
        description="Change to the time preference in the current patient message"
    )
    reply: str = Field(description="Normal reply for none; empty string when escalating")

    @model_validator(mode="after")
    def require_normal_reply(self) -> "AssistantDecision":
        if self.escalation == "none" and not self.reply:
            raise ValueError("a normal reply must not be empty")
        return self
