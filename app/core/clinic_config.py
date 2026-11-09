"""Clinic facts: the only source of truth the assistant may use about the clinic.

Loaded from YAML and validated once at startup, so a broken config fails fast
instead of producing wrong answers in the middle of a conversation.
"""

from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_CLINIC_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "clinic.yaml"

# Stable machine identifiers (e.g. "root_canal_evaluation"), referenced across the config.
Slug = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]


class StrictModel(BaseModel):
    """Unknown keys are errors (catches YAML typos) and values are read-only."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Weekday(StrEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class TimeRange(StrictModel):
    open: time
    close: time

    @field_validator("open", "close", mode="before")
    @classmethod
    def _require_string(cls, value: Any) -> Any:
        # Unquoted YAML 18:00 arrives as the int 1080; Pydantic would silently
        # turn that into 00:18:00. Reject it instead.
        if not isinstance(value, str | time):
            raise ValueError('times must be quoted strings, e.g. "08:00"')
        return value

    @model_validator(mode="after")
    def _open_before_close(self) -> "TimeRange":
        if self.open >= self.close:
            raise ValueError("open must be before close")
        return self


class Dentist(StrictModel):
    id: Slug
    name: str
    specialty: str


class Service(StrictModel):
    id: Slug
    name: str
    description: str
    duration_minutes: int = Field(gt=0)
    dentist_ids: list[Slug] = Field(min_length=1)


class Emergency(StrictModel):
    clinic_phone: str
    public_emergency_number: str


class ClinicConfig(StrictModel):
    name: str
    address: str
    phone: str
    timezone: str
    opening_hours: dict[Weekday, TimeRange]  # missing day = closed
    dentists: list[Dentist] = Field(min_length=1)
    services: list[Service] = Field(min_length=1)
    policies: dict[str, str] = Field(default_factory=dict)
    emergency: Emergency

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _check_ids(self) -> "ClinicConfig":
        dentist_ids = [d.id for d in self.dentists]
        service_ids = [s.id for s in self.services]
        for kind, ids in (("dentist", dentist_ids), ("service", service_ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            if duplicates:
                raise ValueError(f"duplicate {kind} ids: {duplicates}")

        for service in self.services:
            unknown = sorted(set(service.dentist_ids) - set(dentist_ids))
            if unknown:
                raise ValueError(f"service {service.id!r} references unknown dentists: {unknown}")
        return self


def load_clinic_config(path: Path = DEFAULT_CLINIC_CONFIG_PATH) -> ClinicConfig:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)  # safe_load: never constructs arbitrary Python objects
    return ClinicConfig.model_validate(data)
