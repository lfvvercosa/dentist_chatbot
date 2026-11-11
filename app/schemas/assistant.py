"""Validated LLM output, separate from the public HTTP response contract."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.client import EscalationLevel, ResponseLanguage


class AssistantDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    escalation: EscalationLevel = Field(description="Escalation needed in the current context")
    language: ResponseLanguage = Field(description="Patient's language; Portuguese if unclear")
    reply: str = Field(description="Normal reply for none; empty string when escalating")

    @model_validator(mode="after")
    def require_normal_reply(self) -> "AssistantDecision":
        if self.escalation == "none" and not self.reply:
            raise ValueError("a normal reply must not be empty")
        return self
