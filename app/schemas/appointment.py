"""Structured proposals for appointment preferences, separate from calendar actions."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.clinic_config import Slug


class ServicePreferenceUpdate(BaseModel):
    """A change to the requested service; keep differs from explicitly clearing it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["keep", "set", "clear"]
    service_id: Slug | None = Field(description="Configured service ID for set; null otherwise")

    @model_validator(mode="after")
    def require_id_only_for_set(self) -> "ServicePreferenceUpdate":
        if (self.action == "set") != (self.service_id is not None):
            raise ValueError("only set requires a service_id")
        return self


class DentistPreferenceUpdate(BaseModel):
    """A dentist proposal; any is an explicit choice, clear means unanswered again."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["keep", "set", "clear", "any"]
    dentist_id: Slug | None = Field(description="Configured dentist ID for set; null otherwise")

    @model_validator(mode="after")
    def require_id_only_for_set(self) -> "DentistPreferenceUpdate":
        if (self.action == "set") != (self.dentist_id is not None):
            raise ValueError("only set requires a dentist_id")
        return self


class DentistPreference(BaseModel):
    """Accepted preference, including the difference between unanswered and any dentist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["unspecified", "specific", "any"] = "unspecified"
    dentist_id: Slug | None = None

    @model_validator(mode="after")
    def require_id_only_for_specific(self) -> "DentistPreference":
        if (self.kind == "specific") != (self.dentist_id is not None):
            raise ValueError("only a specific preference requires a dentist_id")
        return self
