"""Appointment preferences and candidate slots, separate from calendar write actions."""

from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.clinic_config import Slug
from app.schemas.calendar import TimeInterval

# Demo meanings for named preferences, independent of clinic opening hours.
# Periods include their start and exclude their end; None means the end of the day.
TIME_PERIODS = {
    "morning": (time(0), time(12)),
    "afternoon": (time(12), time(18)),
    "evening": (time(18), None),
}


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


class DatePreference(BaseModel):
    """An inclusive range of clinic-local dates; a single day has equal endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start_date: date
    end_date: date

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def require_calendar_date(cls, value: object) -> date:
        if isinstance(value, str):
            return date.fromisoformat(value)
        if type(value) is date:
            return value
        raise ValueError("date preferences require calendar dates, not timestamps")

    @model_validator(mode="after")
    def require_ordered_range(self) -> "DatePreference":
        if self.start_date > self.end_date:
            raise ValueError("start_date must not follow end_date")
        return self


class DatePreferenceUpdate(BaseModel):
    """A date proposal; clear also removes a previous date after an ambiguous correction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["keep", "set", "clear"]
    date_range: DatePreference | None = Field(
        description="Date range for set, equal endpoints for one day; null otherwise"
    )

    @model_validator(mode="after")
    def require_range_only_for_set(self) -> "DatePreferenceUpdate":
        if (self.action == "set") != (self.date_range is not None):
            raise ValueError("only set requires a date_range")
        return self


class TimePreference(BaseModel):
    """Clinic-local start-time preference; equal window endpoints mean an exact time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["window", "morning", "afternoon", "evening", "any"]
    start_time: time | None = Field(description="Inclusive lower bound for window; null otherwise")
    end_time: time | None = Field(description="Inclusive upper bound for window; null otherwise")

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def require_local_time(cls, value: object) -> time | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = time.fromisoformat(value)
        if type(value) is not time or value.tzinfo is not None:
            raise ValueError("time preferences require local times without a date or offset")
        return value

    @model_validator(mode="after")
    def require_valid_bounds(self) -> "TimePreference":
        if self.kind == "window":
            if self.start_time is None and self.end_time is None:
                raise ValueError("a window requires at least one time bound")
            if (
                self.start_time is not None
                and self.end_time is not None
                and self.start_time > self.end_time
            ):
                raise ValueError("start_time must not follow end_time")
        elif self.start_time is not None or self.end_time is not None:
            raise ValueError("only a window carries time bounds")
        return self


class TimePreferenceUpdate(BaseModel):
    """A time proposal; keep differs from clearing or explicitly accepting any time."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["keep", "set", "clear"]
    preference: TimePreference | None = Field(description="Time preference for set; null otherwise")

    @model_validator(mode="after")
    def require_preference_only_for_set(self) -> "TimePreferenceUpdate":
        if (self.action == "set") != (self.preference is not None):
            raise ValueError("only set requires a time preference")
        return self


class AppointmentSlot(BaseModel):
    """One checked candidate with exact instants; it is neither reserved nor booked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_id: Slug
    dentist_id: Slug
    interval: TimeInterval


class SlotSelectionUpdate(BaseModel):
    """A selection proposal referring to the one-based number in the saved offer list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["keep", "set", "clear"]
    option_number: int | None = Field(
        strict=True, ge=1, description="One-based saved option for set; null otherwise"
    )

    @model_validator(mode="after")
    def require_number_only_for_set(self) -> "SlotSelectionUpdate":
        if (self.action == "set") != (self.option_number is not None):
            raise ValueError("only set requires an option_number")
        return self
