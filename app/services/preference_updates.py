"""Resolve model proposals into a detached booking draft before any accepted state changes."""

from dataclasses import dataclass
from datetime import datetime

from app.core.clinic_config import ClinicConfig
from app.llm.client import LLMError, LLMResult
from app.schemas.appointment import TIME_PERIODS, DentistPreference, TimePreference
from app.schemas.conversation import ConversationState
from app.services.preferences import (
    PreferenceStatus,
    assess_preferences,
    dentist_conflict_reply,
    past_date_reply,
    past_time_reply,
)


@dataclass(frozen=True)
class PreferenceResolution:
    next_state: ConversationState
    status: PreferenceStatus
    clarification: str | None
    has_clear_update: bool
    eligible_dentists: tuple[str, ...]


class PreferenceResolver:
    def __init__(self, clinic: ClinicConfig):
        self._services = {service.id: service for service in clinic.services}
        self._dentists = {dentist.id: dentist for dentist in clinic.dentists}

    def resolve(
        self, state: ConversationState, result: LLMResult, now: datetime
    ) -> PreferenceResolution:
        # History is shared but never edited here. Replace offers rather than mutating their list.
        draft = state.model_copy(
            update={
                "stage": "collecting_preferences",
                "candidate_slots": [],
                "slots_checked_at": None,
            }
        )
        self._merge_updates(draft, result)
        clarification = self._validate_dates_and_times(draft, result, now)
        service = self._services.get(draft.service_id)
        status = assess_preferences(
            service=service,
            dentist=draft.dentist_preference,
            dates=draft.date_preference,
            time=draft.time_preference,
        )
        eligible = (
            tuple(self._dentists[item].name for item in service.dentist_ids) if service else ()
        )
        if clarification is None and status.conflicts:
            clarification = dentist_conflict_reply(
                service,
                self._dentists[draft.dentist_preference.dentist_id],
                eligible,
                result.language,
            )
        return PreferenceResolution(
            next_state=draft,
            status=status,
            clarification=clarification,
            has_clear_update=any(
                proposal is not None and proposal.action == "clear"
                for proposal in (
                    result.service_update,
                    result.dentist_update,
                    result.date_update,
                    result.time_update,
                )
            ),
            eligible_dentists=eligible,
        )

    def _merge_updates(self, draft: ConversationState, result: LLMResult) -> None:
        update = result.service_update
        if update is not None and update.action == "set":
            if update.service_id not in self._services:
                raise LLMError("Claude proposed an unknown service ID")
            draft.service_id = update.service_id
        elif update is not None and update.action == "clear":
            draft.service_id = None
        dentist_update = result.dentist_update
        if dentist_update is not None:
            if dentist_update.action == "set":
                if dentist_update.dentist_id not in self._dentists:
                    raise LLMError("Claude proposed an unknown dentist ID")
                draft.dentist_preference = DentistPreference(
                    kind="specific", dentist_id=dentist_update.dentist_id
                )
            elif dentist_update.action == "any":
                draft.dentist_preference = DentistPreference(kind="any")
            elif dentist_update.action == "clear":
                draft.dentist_preference = DentistPreference()
        date_update = result.date_update
        if date_update is not None:
            if date_update.action == "set":
                draft.date_preference = date_update.date_range
            elif date_update.action == "clear":
                draft.date_preference = None
        time_update = result.time_update
        if time_update is not None:
            if time_update.action == "set":
                draft.time_preference = time_update.preference
            elif time_update.action == "clear":
                draft.time_preference = None

    def _validate_dates_and_times(
        self, draft: ConversationState, result: LLMResult, now: datetime
    ) -> str | None:
        date_reply = None
        time_reply = None
        if draft.date_preference is not None and draft.date_preference.start_date < now.date():
            draft.date_preference = None
            date_reply = past_date_reply(now.date(), result.language)
        if (
            draft.date_preference is not None
            and draft.date_preference.start_date == draft.date_preference.end_date == now.date()
            and draft.time_preference is not None
            and _time_has_passed(draft.time_preference, now)
        ):
            draft.time_preference = None
            time_reply = past_time_reply(result.language)
        return date_reply or time_reply


def _time_has_passed(preference: TimePreference, now: datetime) -> bool:
    if preference.kind == "window":
        return preference.end_time is not None and preference.end_time < now.time()
    if preference.kind in TIME_PERIODS:
        _, end = TIME_PERIODS[preference.kind]
        return end is not None and end <= now.time()
    return False
