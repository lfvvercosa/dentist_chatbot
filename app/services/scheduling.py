"""Route one interpreted turn through the ordinary Python scheduling workflow."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from app.core.clinic_config import ClinicConfig
from app.llm.client import LLMResult
from app.schemas.conversation import ConversationState
from app.services.availability import AvailabilityService, SlotSearchError
from app.services.calendar import CalendarError
from app.services.confirmation import clear_booking, resolve_confirmation, withdraw_booking
from app.services.escalation import EscalationService
from app.services.preference_updates import PreferenceResolver
from app.services.preferences import PreferenceStatus, missing_preference_reply
from app.services.slot_offers import (
    calendar_read_error_reply,
    slot_offer_reply,
    slot_search_error_reply,
)
from app.services.slot_selection import resolve_slot_selection

AvailabilityOutcome = Literal["not_searched", "found", "none_found", "invalid_search", "read_error"]


@dataclass(frozen=True)
class TurnOutcome:
    """Proposed state/reply and diagnostic metadata; ConversationService owns the commit."""

    next_state: ConversationState
    reply: str
    preference_status: PreferenceStatus | None = None
    availability_outcome: AvailabilityOutcome = "not_searched"

    @property
    def offered_slots(self) -> int:
        return len(self.next_state.candidate_slots) if self.availability_outcome == "found" else 0


class SchedulingWorkflow:
    def __init__(
        self, clinic: ClinicConfig, escalation: EscalationService, availability: AvailabilityService
    ):
        self._clinic = clinic
        self._escalation = escalation
        self._availability = availability
        self._preferences = PreferenceResolver(clinic)

    def process(self, state: ConversationState, result: LLMResult, now: datetime) -> TurnOutcome:
        """Return a proposed turn; now is the caller's captured clinic-local clock."""
        escalation_reply = self._escalation.reply(result.escalation, result.language)
        if escalation_reply is not None:
            return TurnOutcome(next_state=clear_booking(state), reply=escalation_reply)
        withdrawal = withdraw_booking(state, result)
        if withdrawal is not None:
            draft, reply = withdrawal
            return TurnOutcome(next_state=draft, reply=reply)
        if result.intent != "booking":
            return TurnOutcome(next_state=state, reply=result.text)
        return self._booking_turn(state, result, now)

    def _booking_turn(
        self, state: ConversationState, result: LLMResult, now: datetime
    ) -> TurnOutcome:
        resolved = self._preferences.resolve(state, result, now)
        draft = resolved.next_state
        reply = resolved.clarification
        availability_outcome: AvailabilityOutcome = "not_searched"
        if reply is None and not resolved.has_clear_update:
            if resolved.status.is_complete:
                selection = (
                    resolve_slot_selection(state, result, self._clinic, now)
                    if not resolved.preferences_changed
                    else None
                )
                if selection is not None:
                    draft, reply = selection
                    confirmation = resolve_confirmation(state, result, self._clinic, now)
                    if confirmation is not None:
                        draft, reply = confirmation
                else:
                    reply, availability_outcome = self._search_slots(draft, result, now)
            else:
                reply = missing_preference_reply(
                    resolved.status, result.language, eligible_dentists=resolved.eligible_dentists
                )
            if reply is not None and result.booking_answer:
                reply = f"{result.booking_answer}\n\n{reply}"
        return TurnOutcome(
            next_state=draft,
            reply=reply or result.text,
            preference_status=resolved.status,
            availability_outcome=availability_outcome,
        )

    def _search_slots(
        self, draft: ConversationState, result: LLMResult, now: datetime
    ) -> tuple[str, AvailabilityOutcome]:
        try:
            draft.candidate_slots = self._availability.find_slots(
                service_id=draft.service_id,
                dentist=draft.dentist_preference,
                dates=draft.date_preference,
                time_preference=draft.time_preference,
                now=now,
            )
        except SlotSearchError as exc:
            return slot_search_error_reply(exc, result.language), "invalid_search"
        except CalendarError:
            return calendar_read_error_reply(result.language), "read_error"
        if draft.candidate_slots:
            draft.stage = "offering_slots"
            draft.slots_checked_at = now.astimezone(UTC)
        outcome: AvailabilityOutcome = "found" if draft.candidate_slots else "none_found"
        return slot_offer_reply(draft.candidate_slots, self._clinic, result.language), outcome
