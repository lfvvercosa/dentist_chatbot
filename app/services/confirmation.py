"""Bind approval to a saved exact summary; no calendar operations belong in this module."""

from datetime import UTC, datetime

from app.core.clinic_config import ClinicConfig
from app.llm.client import LLMResult, ResponseLanguage
from app.schemas.appointment import DentistPreference
from app.schemas.conversation import ConversationState
from app.services.slot_offers import approved_slot_reply, selected_slot_reply


def clear_booking(state: ConversationState) -> ConversationState:
    """Discard a draft while retaining successful conversation history."""
    return state.model_copy(
        update={
            "stage": "chatting",
            "service_id": None,
            "dentist_preference": DentistPreference(),
            "date_preference": None,
            "time_preference": None,
            "candidate_slots": [],
            "slots_checked_at": None,
            "selected_slot": None,
            "booking_proposal": None,
        }
    )


def withdraw_booking(
    state: ConversationState, result: LLMResult
) -> tuple[ConversationState, str] | None:
    """Honor explicit abandonment, or a bound refusal with no competing corrections.

    Withdrawal can discard even an expired draft; it never cancels a calendar event.
    Escalation has already been handled by the caller.
    """
    update = result.confirmation_update
    if update is None:
        return None
    bound_decline = (
        update.action == "decline"
        and result.intent == "booking"
        and state.stage in ("awaiting_confirmation", "ready_to_book")
        and state.booking_proposal is not None
        and update.proposal_id == state.booking_proposal.proposal_id
        and not any(
            proposal is not None and proposal.action != "keep"
            for proposal in (
                result.service_update,
                result.dentist_update,
                result.date_update,
                result.time_update,
                result.slot_update,
            )
        )
    )
    if update.action != "abandon" and not bound_decline:
        return None
    return _withdrawn(state, result.language)


def _withdrawn(
    state: ConversationState, language: ResponseLanguage
) -> tuple[ConversationState, str]:
    reply = (
        "O pedido de agendamento foi descartado. Nada foi reservado ou agendado. "
        "Posso ajudar com outra coisa?"
        if language == "pt"
        else "The appointment request has been discarded. Nothing has been reserved or booked. "
        "Can I help with anything else?"
    )
    return clear_booking(state), reply


def resolve_confirmation(
    state: ConversationState, result: LLMResult, clinic: ClinicConfig, now: datetime
) -> tuple[ConversationState, str] | None:
    """Resolve consent against the pre-turn summary after complete, unchanged preferences.

    Inspect the accepted state from before this turn, so a newly selected summary cannot
    also be approved in the same message. Model labels alone never authorize a write.
    """
    update = result.confirmation_update
    proposal = state.booking_proposal
    if (
        update is None
        or update.action not in ("confirm", "decline")
        or state.stage not in ("awaiting_confirmation", "ready_to_book")
        or proposal is None
        or proposal.slot != state.selected_slot
        or proposal.slot not in state.candidate_slots
        or proposal.slot.interval.start <= now.astimezone(UTC)
        or (result.slot_update is not None and result.slot_update.action != "keep")
    ):
        return None
    if update.proposal_id != proposal.proposal_id:
        prefix = (
            "Essa resposta se refere a outro resumo. O pedido atual permanece como segue:"
            if result.language == "pt"
            else "That response refers to a different summary. "
            "The current request remains as follows:"
        )
        renderer = approved_slot_reply if state.stage == "ready_to_book" else selected_slot_reply
        return state, f"{prefix}\n\n{renderer(proposal.slot, clinic, result.language)}"
    if update.action == "decline":
        # Redundant same-value preference proposals can reach this path after validation.
        return _withdrawn(state, result.language)
    approved = state.model_copy(update={"stage": "ready_to_book"})
    return approved, approved_slot_reply(proposal.slot, clinic, result.language)
