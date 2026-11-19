"""Consent is bound to a prior exact summary; approval never writes a calendar event."""

import json
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.api.routes import get_conversation_service
from app.llm.client import ChatMessage, LLMError
from app.main import app
from app.schemas.appointment import (
    BookingProposal,
    ConfirmationUpdate,
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    SlotSelectionUpdate,
    TimePreference,
    TimePreferenceUpdate,
)


def respond(fake_llm, state, action="confirm", proposal_id=None):
    fake_llm.confirmation_update = ConfirmationUpdate(
        action=action, proposal_id=proposal_id or state.booking_proposal.proposal_id
    )


@pytest.mark.parametrize("language", ["pt", "en"])
def test_matching_approval_records_exact_proposal_without_reservation(pending, fake_llm, language):
    service, calendar, _ = pending
    state = service._states["one"]
    proposal = state.booking_proposal
    fake_llm.language = language
    respond(fake_llm, state)
    reply = service.handle_message("one", "Yes, book those exact details")
    assert state.stage == "ready_to_book" and state.booking_proposal == proposal
    assert state.selected_slot == proposal.slot and calendar.reads == 1
    assert ("Aprovação registrada" if language == "pt" else "Approval recorded") in reply
    assert "10:00:00-03:00 → 2026-11-19 10:30:00-03:00" in reply
    assert "Dr. Ana Souza" in reply and "Initial consultation" in reply
    assert str(proposal.proposal_id) not in reply
    assert (
        "nada foi reservado ou agendado"
        if language == "pt"
        else "nothing has been reserved or booked"
    ) in reply.lower()
    assert state.messages[-1] == ChatMessage("assistant", reply)
    assert calendar.get_busy_intervals("ana", proposal.slot.interval) == []


def test_repeated_approval_is_idempotent_and_ambiguous_consent_does_not_approve(pending, fake_llm):
    service, calendar, _ = pending
    state = service._states["one"]
    proposal = state.booking_proposal
    fake_llm.confirmation_update = ConfirmationUpdate(action="keep", proposal_id=None)
    clarification = service.handle_message("one", "Maybe, I am not sure")
    assert state.stage == "awaiting_confirmation" and "Você confirma" in clarification
    assert state.booking_proposal == proposal
    respond(fake_llm, state)
    approved = service.handle_message("one", "Yes")
    assert service.handle_message("one", "Yes, again") == approved
    assert state.stage == "ready_to_book" and state.booking_proposal == proposal
    assert calendar.reads == 1


def test_selection_and_consent_in_one_turn_only_ask_for_confirmation(offered, fake_llm):
    service, calendar, _ = offered
    fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=2)
    fake_llm.confirmation_update = ConfirmationUpdate(action="confirm", proposal_id=uuid4())
    reply = service.handle_message("one", "Yes, option 2, book it now")
    state = service._states["one"]
    assert state.stage == "awaiting_confirmation" and state.booking_proposal is not None
    assert state.booking_proposal.proposal_id != fake_llm.confirmation_update.proposal_id
    assert "Você confirma" in reply and calendar.reads == 1


def test_consent_without_selection_cannot_choose_the_only_option(offered, fake_llm):
    service, calendar, _ = offered
    state = service._states["one"]
    state.candidate_slots = state.candidate_slots[:1]
    fake_llm.confirmation_update = ConfirmationUpdate(action="confirm", proposal_id=uuid4())
    service.handle_message("one", "Yes")
    assert state.stage == "offering_slots" and state.selected_slot is None
    assert state.booking_proposal is None and calendar.reads == 2


@pytest.mark.parametrize("number", [2, 3])
def test_reselection_revokes_approval_and_old_summary_cannot_approve_new_one(
    pending, fake_llm, number
):
    service, calendar, _ = pending
    state = service._states["one"]
    old = state.booking_proposal
    respond(fake_llm, state)
    service.handle_message("one", "Yes")
    fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=number)
    service.handle_message("one", "Choose that option instead, yes")
    current = state.booking_proposal
    assert state.stage == "awaiting_confirmation" and current.proposal_id != old.proposal_id
    fake_llm.slot_update = None
    reply = service.handle_message("one", "Yes to the earlier summary")
    assert "outro resumo" in reply and state.stage == "awaiting_confirmation"
    assert state.booking_proposal == current and calendar.reads == 1
    respond(fake_llm, state)
    service.handle_message("one", "Yes to these current details")
    assert state.stage == "ready_to_book" and state.booking_proposal == current


@pytest.mark.parametrize("action", ["confirm", "decline"])
def test_cross_conversation_reference_cannot_approve_or_discard_current_summary(
    pending, fake_llm, action
):
    service, calendar, _ = pending
    original = service._states["one"].model_copy(deep=True)
    other = original.model_copy(
        update={"messages": [], "booking_proposal": BookingProposal(slot=original.selected_slot)}
    )
    service._states["two"] = other
    respond(fake_llm, original, action)
    reply = service.handle_message("two", "A response to the other conversation")
    assert "outro resumo" in reply and other.stage == "awaiting_confirmation"
    assert other.booking_proposal.proposal_id != original.booking_proposal.proposal_id
    assert service._states["one"] == original and calendar.reads == 1


@pytest.mark.parametrize("field", ["service", "dentist", "date", "time", "clear", "slot_clear"])
@pytest.mark.parametrize("approved_before", [False, True])
def test_yes_with_corrections_revokes_proposal_and_cannot_record_approval(
    pending, fake_llm, field, approved_before
):
    service, calendar, _ = pending
    state = service._states["one"]
    respond(fake_llm, state)
    if approved_before:
        service.handle_message("one", "Yes to the original summary")
    updates = {
        "service": ("service_update", ServicePreferenceUpdate(action="set", service_id="cleaning")),
        "dentist": ("dentist_update", DentistPreferenceUpdate(action="set", dentist_id="bruno")),
        "date": (
            "date_update",
            DatePreferenceUpdate(
                action="set",
                date_range=DatePreference(start_date="2026-11-20", end_date="2026-11-20"),
            ),
        ),
        "time": (
            "time_update",
            TimePreferenceUpdate(
                action="set",
                preference=TimePreference(kind="afternoon", start_time=None, end_time=None),
            ),
        ),
        "clear": ("time_update", TimePreferenceUpdate(action="clear", preference=None)),
        "slot_clear": ("slot_update", SlotSelectionUpdate(action="clear", option_number=None)),
    }
    name, update = updates[field]
    setattr(fake_llm, name, update)
    service.handle_message("one", "Yes, but change that detail")
    assert state.stage != "ready_to_book" and state.booking_proposal is None
    assert state.selected_slot is None
    assert calendar.reads == (1 if field in ("clear", "slot_clear") else 2)


@pytest.mark.parametrize("mutation", ["elapsed", "mismatched_slot", "wrong_stage"])
def test_approval_requires_future_matching_slot_and_awaiting_stage(pending, fake_llm, mutation):
    service, calendar, clock = pending
    state = service._states["one"]
    respond(fake_llm, state)
    old_id = state.booking_proposal.proposal_id
    if mutation == "elapsed":
        clock[0] = datetime(2026, 11, 19, 10, tzinfo=ZoneInfo("America/Sao_Paulo"))
    elif mutation == "mismatched_slot":
        state.selected_slot = state.candidate_slots[2]
    else:
        state.stage = "offering_slots"
    service.handle_message("one", "Yes")
    assert state.stage != "ready_to_book"
    if mutation == "elapsed":
        assert state.booking_proposal is None and state.stage == "offering_slots"
        assert calendar.reads == 2
    elif mutation == "mismatched_slot":
        assert state.booking_proposal.proposal_id != old_id and calendar.reads == 1


@pytest.mark.parametrize("language", ["pt", "en"])
@pytest.mark.parametrize("expired", [False, True])
def test_bound_refusal_discards_draft_even_after_expiry_and_preserves_history(
    pending, fake_llm, language, expired
):
    service, calendar, clock = pending
    state = service._states["one"]
    history = list(state.messages)
    fake_llm.language = language
    respond(fake_llm, state, "decline")
    if expired:
        clock[0] = datetime(2026, 11, 21, 12, tzinfo=ZoneInfo("America/Sao_Paulo"))
    reply = service.handle_message("one", "No, I do not want it")
    assert state.stage == "chatting" and state.booking_proposal is None
    assert state.selected_slot is None and state.service_id is None
    assert state.dentist_preference.kind == "unspecified"
    assert state.date_preference is None and state.time_preference is None
    assert state.candidate_slots == [] and state.slots_checked_at is None
    assert state.messages[:-2] == history and state.messages[-1].content == reply
    assert calendar.reads == 1


@pytest.mark.parametrize("phase", ["collecting", "offering", "awaiting", "approved"])
def test_abandonment_can_end_any_draft_without_cancelling_an_event(offered, fake_llm, phase):
    service, calendar, _ = offered
    conversation_id = "one"
    if phase == "collecting":
        conversation_id = "new"
        service.handle_message(conversation_id, "Start booking")
    elif phase in ("awaiting", "approved"):
        fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=2)
        service.handle_message("one", "Option 2")
        fake_llm.slot_update = None
        if phase == "approved":
            respond(fake_llm, service._states["one"])
            service.handle_message("one", "Yes")
    reads = calendar.reads
    fake_llm.intent = "other"
    fake_llm.confirmation_update = ConfirmationUpdate(action="abandon", proposal_id=None)
    reply = service.handle_message(conversation_id, "Forget this appointment request")
    state = service._states[conversation_id]
    assert state.stage == "chatting" and state.booking_proposal is None
    assert state.selected_slot is None and state.service_id is None
    assert state.candidate_slots == [] and calendar.reads == reads
    assert "descartado" in reply


def test_negative_answer_with_a_new_choice_asks_for_new_summary_instead_of_discarding(
    pending, fake_llm
):
    service, calendar, _ = pending
    state = service._states["one"]
    respond(fake_llm, state, "decline")
    fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=3)
    service.handle_message("one", "No, option 3 instead")
    assert state.stage == "awaiting_confirmation"
    assert state.booking_proposal.slot == state.candidate_slots[2] and calendar.reads == 1


@pytest.mark.parametrize("phase", ["awaiting", "approved"])
def test_faq_preserves_proposal_and_cannot_supply_confirmation(pending, fake_llm, phase):
    service, calendar, _ = pending
    state = service._states["one"]
    respond(fake_llm, state)
    if phase == "approved":
        service.handle_message("one", "Yes")
    before = state.model_copy(deep=True)
    fake_llm.intent = "other"
    reply = service.handle_message("one", "Yes, do you open on Saturday?")
    assert reply.startswith("reply ") and state.stage == before.stage
    assert state.booking_proposal == before.booking_proposal and calendar.reads == 1
    snapshot = json.loads(fake_llm.calls[-1][0].rsplit("\n", 1)[1])["current_workflow_state"]
    assert snapshot["booking_proposal"] == before.booking_proposal.model_dump(mode="json")


@pytest.mark.parametrize("approved_before", [False, True])
def test_failed_model_turn_preserves_summary_and_can_recover(pending, fake_llm, approved_before):
    service, calendar, _ = pending
    state = service._states["one"]
    respond(fake_llm, state)
    if approved_before:
        service.handle_message("one", "Yes to the original summary")
    before = state.model_copy(deep=True)
    fake_llm.error = LLMError("provider failure")
    with pytest.raises(LLMError):
        service.handle_message("one", "Failed approval")
    assert state == before
    fake_llm.error = None
    service.handle_message("one", "Yes")
    assert state.stage == "ready_to_book" and calendar.reads == 1


def test_refusal_with_redundant_unchanged_preference_discards_the_draft(pending, fake_llm):
    service, calendar, _ = pending
    state = service._states["one"]
    respond(fake_llm, state, "decline")
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )
    service.handle_message("one", "No to that morning appointment")
    assert state.stage == "chatting" and state.booking_proposal is None
    assert state.selected_slot is None and calendar.reads == 1


@pytest.mark.parametrize("action", ["confirm", "abandon"])
def test_escalation_overrides_consent_and_abandonment(
    pending, fake_llm, action, escalation_service
):
    service, calendar, _ = pending
    state = service._states["one"]
    fake_llm.confirmation_update = ConfirmationUpdate(
        action=action,
        proposal_id=state.booking_proposal.proposal_id if action == "confirm" else None,
    )
    fake_llm.escalation = "urgent"
    reply = service.handle_message("one", "An urgent concern")
    assert reply == escalation_service.reply("urgent", "pt")
    assert state.stage == "chatting" and state.booking_proposal is None
    assert state.selected_slot is None and calendar.reads == 1


def test_confirmation_http_reply_is_backend_owned_and_preserves_mixed_clinic_answer(
    pending, fake_llm, monkeypatch
):
    service, calendar, _ = pending
    monkeypatch.setitem(app.dependency_overrides, get_conversation_service, lambda: service)
    respond(fake_llm, service._states["one"])
    fake_llm.booking_answer = "I do not have pricing information."
    response = TestClient(app).post(
        "/chat", json={"conversation_id": "one", "message": "Yes. Price?"}
    )
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"conversation_id", "reply"}
    assert body["reply"].startswith(fake_llm.booking_answer + "\n\nAprovação registrada")
    assert service._states["one"].stage == "ready_to_book" and calendar.reads == 1
