"""Assess accepted preferences and ask for the next missing appointment detail."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from app.core.clinic_config import Service
from app.llm.client import ResponseLanguage
from app.schemas.appointment import DatePreference, DentistPreference, TimePreference

PreferenceField = Literal["service", "dentist", "date", "time"]
PreferenceConflict = Literal["service_dentist"]

_MISSING_QUESTIONS = {
    "pt": {
        "service": "Qual serviço você deseja agendar?",
        "dentist": (
            "Você tem preferência por um dentista ou aceita qualquer profissional habilitado "
            "para esse serviço? Profissionais habilitados: {dentists}."
        ),
        "date": "Qual data ou intervalo de datas você prefere?",
        "time": "Qual horário ou período do dia você prefere, ou pode ser qualquer horário?",
    },
    "en": {
        "service": "Which service would you like to schedule?",
        "dentist": (
            "Do you prefer a particular dentist, or any dentist eligible for this service? "
            "Eligible dentists: {dentists}."
        ),
        "date": "Which date or date range would you prefer?",
        "time": "What time or part of the day do you prefer, or is any time suitable?",
    },
}


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


def missing_preference_reply(
    status: PreferenceStatus,
    language: ResponseLanguage,
    *,
    eligible_dentists: Sequence[str],
) -> str | None:
    """Ask only for the first missing detail; conflicts and complete drafts have other replies."""
    if not status.missing or status.conflicts:
        return None
    template = _MISSING_QUESTIONS[language][status.missing[0]]
    return template.format(dentists=", ".join(eligible_dentists))
