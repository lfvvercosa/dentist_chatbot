"""A workflow proposes state changes; accepting them belongs to ConversationService."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.llm.client import ChatMessage, LLMError, LLMResult
from app.schemas.appointment import (
    AppointmentSlot,
    DatePreference,
    DentistPreference,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.schemas.calendar import TimeInterval
from app.schemas.conversation import ConversationState
from app.services.scheduling import SchedulingWorkflow

NOW = datetime(2026, 11, 18, 9, tzinfo=ZoneInfo("America/Sao_Paulo"))


@pytest.fixture
def accepted():
    return ConversationState(
        messages=[
            ChatMessage("user", "Complete"),
            ChatMessage("assistant", "Previous checked offer"),
        ],
        stage="offering_slots",
        service_id="consultation",
        dentist_preference=DentistPreference(kind="specific", dentist_id="ana"),
        date_preference=DatePreference(start_date="2026-11-19", end_date="2026-11-19"),
        time_preference=TimePreference(kind="morning", start_time=None, end_time=None),
        candidate_slots=[
            AppointmentSlot(
                service_id="consultation",
                dentist_id="ana",
                interval=TimeInterval(
                    start="2026-11-19T08:00:00-03:00", end="2026-11-19T08:30:00-03:00"
                ),
            )
        ],
        slots_checked_at=NOW,
    )


@pytest.fixture
def workflow(clinic_config, escalation_service, availability_factory):
    return SchedulingWorkflow(
        clinic_config, escalation_service, availability_factory(clinic_config)
    )


def test_workflow_proposes_new_offers_without_changing_accepted_state_or_history(
    workflow, accepted
):
    before = accepted.model_copy(deep=True)
    history = accepted.messages
    previous_offers = accepted.candidate_slots
    decision = LLMResult(
        text="Unused model reply",
        model="fake",
        input_tokens=0,
        output_tokens=0,
        intent="booking",
        time_update=TimePreferenceUpdate(
            action="set",
            preference=TimePreference(kind="window", start_time="14:07", end_time="14:07"),
        ),
    )
    outcome = workflow.process(accepted, decision, NOW)
    assert accepted == before and accepted.messages is history
    assert accepted.candidate_slots is previous_offers
    assert outcome.next_state is not accepted
    assert outcome.next_state.candidate_slots != previous_offers
    assert "14:07:00-03:00" in outcome.reply
    assert outcome.next_state.messages == history
    assert outcome.availability_outcome == "found"


def test_later_invalid_proposal_cannot_commit_an_earlier_valid_update(workflow, accepted):
    before = accepted.model_copy(deep=True)
    decision = LLMResult(
        text="Unused reply",
        model="fake",
        input_tokens=0,
        output_tokens=0,
        intent="booking",
        service_update=ServicePreferenceUpdate(action="set", service_id="cleaning"),
        dentist_update=DentistPreferenceUpdate(action="set", dentist_id="unknown"),
    )
    with pytest.raises(LLMError, match="unknown dentist ID"):
        workflow.process(accepted, decision, NOW)
    assert accepted == before
