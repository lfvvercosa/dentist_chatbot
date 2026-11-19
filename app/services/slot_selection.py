"""Resolve interpreted choices against saved offers without reordering or reading a calendar."""

from datetime import UTC, datetime

from app.core.clinic_config import ClinicConfig
from app.llm.client import LLMResult
from app.schemas.conversation import ConversationState
from app.services.slot_offers import invalid_slot_reply, selected_slot_reply


def resolve_slot_selection(
    state: ConversationState, result: LLMResult, clinic: ClinicConfig, now: datetime
) -> tuple[ConversationState, str] | None:
    """Return a selection/clarification, or None to let the workflow search afresh.

    Called only after preferences are complete, valid, unchanged, and not being cleared.
    Saved offers retain their original order and check time; selecting one is not a new read.
    """
    if (
        state.stage not in ("offering_slots", "slot_selected")
        or not state.candidate_slots
        or state.slots_checked_at is None
    ):
        return None
    now = now.astimezone(UTC)
    if not any(slot.interval.start > now for slot in state.candidate_slots):
        return None
    update = result.slot_update
    if update is not None and update.action == "clear":
        draft = state.model_copy(update={"stage": "offering_slots", "selected_slot": None})
        return draft, result.text
    if update is not None and update.action == "set":
        if update.option_number > len(state.candidate_slots):
            draft = state.model_copy(update={"stage": "offering_slots", "selected_slot": None})
            return draft, invalid_slot_reply(len(state.candidate_slots), result.language)
        slot = state.candidate_slots[update.option_number - 1]
    else:
        slot = state.selected_slot
    if slot is None or slot not in state.candidate_slots or slot.interval.start <= now:
        return None
    draft = state.model_copy(update={"stage": "slot_selected", "selected_slot": slot})
    return draft, selected_slot_reply(slot, clinic, result.language)
