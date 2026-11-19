"""Concrete calendar intervals, separate from a patient's date/time preferences."""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class TimeInterval(BaseModel):
    """A nonempty [start, end) interval of instants, normalized to UTC."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime

    @field_validator("start", "end", mode="before")
    @classmethod
    def require_aware_datetime(cls, value: object) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("calendar intervals require timezone-aware datetimes")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_positive_duration(self) -> "TimeInterval":
        if self.start >= self.end:
            raise ValueError("interval start must be before end")
        return self

    def overlaps(self, other: "TimeInterval") -> bool:
        """Intervals that only touch at an endpoint do not overlap."""
        return self.start < other.end and other.start < self.end
