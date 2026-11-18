"""Deterministic completeness checks over already-validated appointment preferences."""

from dataclasses import dataclass
from typing import Literal

from app.core.clinic_config import Service
from app.schemas.appointment import DatePreference, DentistPreference, TimePreference

PreferenceField = Literal["service", "dentist", "date", "time"]
PreferenceConflict = Literal["service_dentist"]


@dataclass(frozen=True)
class PreferenceStatus:
    """A derived result, not saved authorization or a claim of available appointments."""

    missing: tuple[PreferenceField, ...]
    conflicts: tuple[PreferenceConflict, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing and not self.conflicts


def assess_preferences(
    *,
    service: Service | None,
    dentist: DentistPreference,
    dates: DatePreference | None,
    time: TimePreference | None,
) -> PreferenceStatus:
    """Check presence and compatibility after configured-ID and date/time validation.

    Missing fields follow a stable order for the later follow-up-question step.
    This function neither checks availability nor changes the supplied preferences.
    """
    missing: list[PreferenceField] = []
    if service is None:
        missing.append("service")
    if dentist.kind == "unspecified":
        missing.append("dentist")
    if dates is None:
        missing.append("date")
    if time is None:
        missing.append("time")

    conflicts: tuple[PreferenceConflict, ...] = ()
    if (
        service is not None
        and dentist.kind == "specific"
        and dentist.dentist_id not in service.dentist_ids
    ):
        conflicts = ("service_dentist",)
    return PreferenceStatus(missing=tuple(missing), conflicts=conflicts)
