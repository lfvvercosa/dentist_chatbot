"""Resolve interpreted choices against saved offers without reordering or reading a calendar."""

from datetime import UTC, datetime

from app.core.clinic_config import ClinicConfig
from app.llm.client import LLMResult
from app.schemas.appointment import BookingProposal
from app.schemas.conversation import ConversationState
from app.services.slot_offers import approved_slot_reply, invalid_slot_reply, selected_slot_reply


def resolve_slot_selection(
    state: ConversationState, result: LLMResult, clinic: ClinicConfig, now: datetime
) -> tuple[ConversationState, str] | None:
    """Return a selection/clarification, or None to let the workflow search afresh.

    Called only after preferences are complete, valid, unchanged, and not being cleared.
    Saved offers retain their original order and check time; selecting one is not a new read.
    """
    if (
        state.stage not in ("offering_slots", "awaiting_confirmation", "ready_to_book")
        or not state.candidate_slots
        or state.slots_checked_at is None
    ):
        return None
    now = now.astimezone(UTC)
    if not any(slot.interval.start > now for slot in state.candidate_slots):
        return None
    update = result.slot_update
    if update is not None and update.action == "clear":
        draft = state.model_copy(
            update={"stage": "offering_slots", "selected_slot": None, "booking_proposal": None}
        )
        return draft, result.text
    if update is not None and update.action == "set":
        if update.option_number > len(state.candidate_slots):
            draft = state.model_copy(
                update={"stage": "offering_slots", "selected_slot": None, "booking_proposal": None}
            )
            return draft, invalid_slot_reply(len(state.candidate_slots), result.language)
        slot = state.candidate_slots[update.option_number - 1]
    else:
        slot = state.selected_slot
    if slot is None or slot not in state.candidate_slots or slot.interval.start <= now:
        return None
    proposal = state.booking_proposal
    # A fresh choice always needs a fresh summary, including a re-selection of the same slot.
    is_new_selection = update is not None and update.action == "set"
    if is_new_selection or proposal is None or proposal.slot != slot:
        proposal = BookingProposal(slot=slot)
        stage = "awaiting_confirmation"
    else:
        stage = state.stage if state.stage == "ready_to_book" else "awaiting_confirmation"
    draft = state.model_copy(
        update={"stage": stage, "selected_slot": slot, "booking_proposal": proposal}
    )
    renderer = approved_slot_reply if stage == "ready_to_book" else selected_slot_reply
    return draft, renderer(slot, clinic, result.language)
