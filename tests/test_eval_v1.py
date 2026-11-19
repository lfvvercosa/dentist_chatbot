"""Evaluation plumbing tests use FakeLLM; importing the runner makes no live calls."""

import json

import pytest
from pydantic import ValidationError

from app.llm.client import ChatMessage, LLMError
from app.schemas.appointment import (
    DatePreference,
    DatePreferenceUpdate,
    DentistPreferenceUpdate,
    ServicePreferenceUpdate,
    SlotSelectionUpdate,
    TimePreference,
    TimePreferenceUpdate,
)
from app.services.conversation import ConversationService
from scripts.eval_v1 import EvalCase, EvalSuite, RecordingLLM, run_case


def test_evaluation_records_backend_question_and_its_actual_history(
    fake_llm, escalation_service, clinic_config, fixed_clock, availability_factory
):
    fake_llm.intent = "booking"
    fake_llm.booking_answer = "We open at 08:00 on Mondays."
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        clock=fixed_clock,
        availability=availability_factory(clinic_config),
    )
    result = run_case(
        EvalCase(id="missing", messages=["Book", "Continue"], criteria=["Ask missing service"]),
        service,
        llm,
    )
    question = "We open at 08:00 on Mondays.\n\nQual serviço você deseja agendar?"
    assert [turn["reply"] for turn in result["turns"]] == [question, question]
    assert llm.last_result.text == "reply 2"
    assert result["turns"][0]["decision"]["booking_answer"] == fake_llm.booking_answer
    assert fake_llm.calls[1][1][1] == ChatMessage("assistant", question)


def test_evaluation_records_checked_offers_and_serializable_saved_slot_context(
    fake_llm, escalation_service, clinic_config, fixed_clock, availability_factory
):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="any", dentist_id=None)
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-18", end_date="2026-11-18")
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "base prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
        clock=fixed_clock,
    )
    result = run_case(
        EvalCase(
            id="mock-offers",
            messages=["Complete", "Continue"],
            criteria=["Offer checked demo slots"],
        ),
        service,
        llm,
    )
    first, second = result["turns"]
    assert first["reply"].startswith("Opções verificadas")
    context = json.loads(second["system_prompt"].rsplit("\n", 1)[1])["current_workflow_state"]
    assert context["stage"] == "offering_slots" and len(context["candidate_slots"]) == 3
    assert context["slots_checked_at"] is not None
    assert fake_llm.calls[1][1][1] == ChatMessage("assistant", first["reply"])
    json.dumps(result)


def test_eval_preserves_history_but_isolates_cases_and_leaves_grading_pending(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "real prompt placeholder",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(id="memory", messages=["I am Alex", "My name?"], criteria=["Recall Alex"])

    result = run_case(case, service, llm)
    other = run_case(case, service, llm)

    assert fake_llm.calls[1][1] == [
        ChatMessage("user", "I am Alex"),
        ChatMessage("assistant", "reply 1"),
        ChatMessage("user", "My name?"),
    ]
    assert fake_llm.calls[2][1] == [ChatMessage("user", "I am Alex")]
    assert result["conversation_id"] != other["conversation_id"]
    assert result["execution_status"] == "completed"
    assert result["review"]["verdict"] == "pending"
    assert result["turns"][1]["reply"] == "reply 2"
    assert result["turns"][1]["usage"] == {
        "model": "fake-model",
        "input_tokens": 10,
        "output_tokens": 5,
    }


def test_execution_error_skips_dependent_turns_without_becoming_behavior_failure(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(id="booking", messages=["Book", "Confirm"], criteria=["Cannot book"])
    fake_llm.error = LLMError("API unavailable")

    result = run_case(case, service, llm)

    assert len(fake_llm.calls) == 1
    assert result["execution_status"] == "error"
    assert result["review"]["verdict"] == "pending"
    assert result["turns"][0]["error"] == "API unavailable"
    assert result["turns"][0]["system_prompt"] == fake_llm.calls[0][0]
    assert "usage" not in result["turns"][0]

    fake_llm.error = None
    assert run_case(case, service, llm)["execution_status"] == "completed"


def test_duplicate_case_ids_are_rejected_before_a_run():
    case = EvalCase(id="same", messages=["Hello"], criteria=["Respond"])
    with pytest.raises(ValidationError, match="ids must be unique"):
        EvalSuite(shared_criteria=["No invented facts"], cases=[case, case])


def test_escalation_check_compares_the_model_decision_with_expected_level(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(
        id="urgent", messages=["Help"], criteria=["Escalate"], expected_escalation=["urgent"]
    )

    mismatch = run_case(case, service, llm)
    assert mismatch["turns"][0]["escalation_check"] == {"expected": "urgent", "passed": False}

    fake_llm.escalation = "urgent"
    match = run_case(case, service, llm)
    assert match["turns"][0]["escalation_check"]["passed"] is True
    assert match["turns"][0]["decision"] == {
        "escalation": "urgent",
        "language": "pt",
        "intent": "other",
        "service_update": None,
        "dentist_update": None,
        "date_update": None,
        "time_update": None,
        "booking_answer": None,
        "slot_update": None,
    }
    assert match["turns"][0]["reply"] == escalation_service.reply("urgent", "pt")
    assert match["review"]["verdict"] == "pending"  # Classification check is not full review.


def test_expected_escalation_requires_one_level_per_turn():
    with pytest.raises(ValidationError, match="one entry per message"):
        EvalCase(
            id="incomplete",
            messages=["Hi", "Help"],
            criteria=["Escalate"],
            expected_escalation=["none"],
        )


def test_service_update_is_saved_as_json_serializable_evaluation_evidence(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(id="service", messages=["A cleaning"], criteria=["Collect service"])

    result = run_case(case, service, llm)

    assert result["turns"][0]["decision"]["service_update"] == {
        "action": "set",
        "service_id": "cleaning",
    }
    json.dumps(result)


@pytest.mark.parametrize("action", ["set", "any"])
def test_dentist_proposal_is_saved_as_json_serializable_evaluation_evidence(
    fake_llm, escalation_service, clinic_config, action, availability_factory
):
    dentist_id = "ana" if action == "set" else None
    fake_llm.intent = "booking"
    fake_llm.dentist_update = DentistPreferenceUpdate(action=action, dentist_id=dentist_id)
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(id="dentist", messages=["My preference"], criteria=["Collect dentist"])

    result = run_case(case, service, llm)

    assert result["turns"][0]["decision"]["dentist_update"] == {
        "action": action,
        "dentist_id": dentist_id,
    }
    json.dumps(result)


def test_evaluation_saves_the_exact_prompt_with_state_for_each_turn(
    fake_llm, escalation_service, clinic_config, availability_factory
):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="cleaning")
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "base prompt",
        escalation_service,
        clinic_config,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(id="context", messages=["A cleaning", "Tomorrow"], criteria=["Keep service"])

    result = run_case(case, service, llm)

    prompts = [turn["system_prompt"] for turn in result["turns"]]
    assert prompts == [system for system, _ in fake_llm.calls]
    assert [
        json.loads(prompt.rsplit("\n", 1)[1])["current_workflow_state"] for prompt in prompts
    ] == [
        {
            "stage": "chatting",
            "service_id": None,
            "dentist_preference": {"kind": "unspecified", "dentist_id": None},
            "date_preference": None,
            "time_preference": None,
            "candidate_slots": [],
            "slots_checked_at": None,
            "selected_slot": None,
        },
        {
            "stage": "collecting_preferences",
            "service_id": "cleaning",
            "dentist_preference": {"kind": "unspecified", "dentist_id": None},
            "date_preference": None,
            "time_preference": None,
            "candidate_slots": [],
            "slots_checked_at": None,
            "selected_slot": None,
        },
    ]


def test_evaluation_saves_json_dates_and_the_exact_clinic_clock_context(
    fake_llm, escalation_service, clinic_config, fixed_clock, availability_factory
):
    fake_llm.intent = "booking"
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-18", end_date="2026-11-20")
    )
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        clock=fixed_clock,
        availability=availability_factory(clinic_config),
    )
    case = EvalCase(
        id="dates", messages=["Between tomorrow and Friday"], criteria=["Collect dates"]
    )

    result = run_case(case, service, llm)

    turn = result["turns"][0]
    assert turn["decision"]["date_update"] == {
        "action": "set",
        "date_range": {"start_date": "2026-11-18", "end_date": "2026-11-20"},
    }
    context = json.loads(turn["system_prompt"].rsplit("\n", 1)[1])
    assert context["current_time"]["today"] == "2026-11-17"
    assert context["current_time"]["timezone"] == clinic_config.timezone
    json.dumps(result)


def test_evaluation_serializes_time_proposals_and_saved_time_context(
    fake_llm, escalation_service, clinic_config, fixed_clock, availability_factory
):
    fake_llm.intent = "booking"
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="window", start_time="14:00", end_time="16:00")
    )
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "prompt",
        escalation_service,
        clinic_config,
        clock=fixed_clock,
        availability=availability_factory(clinic_config),
    )
    result = run_case(
        EvalCase(id="times", messages=["Entre 14 e 16", "Continue"], criteria=["Keep time"]),
        service,
        llm,
    )
    first, second = result["turns"]
    preference = {"kind": "window", "start_time": "14:00:00", "end_time": "16:00:00"}
    assert first["decision"]["time_update"] == {"action": "set", "preference": preference}
    context = json.loads(second["system_prompt"].rsplit("\n", 1)[1])
    assert context["current_workflow_state"]["time_preference"] == preference
    json.dumps(result)


def test_evaluation_records_selection_proposal_summary_and_selected_snapshot(
    fake_llm, escalation_service, clinic_config, fixed_clock, availability_factory, monkeypatch
):
    fake_llm.intent = "booking"
    fake_llm.service_update = ServicePreferenceUpdate(action="set", service_id="consultation")
    fake_llm.dentist_update = DentistPreferenceUpdate(action="set", dentist_id="ana")
    fake_llm.date_update = DatePreferenceUpdate(
        action="set", date_range=DatePreference(start_date="2026-11-18", end_date="2026-11-18")
    )
    fake_llm.time_update = TimePreferenceUpdate(
        action="set", preference=TimePreference(kind="morning", start_time=None, end_time=None)
    )
    original_generate = fake_llm.generate

    def respond(system, messages):
        if len(fake_llm.calls) == 1:
            for field in ("service_update", "dentist_update", "date_update", "time_update"):
                setattr(fake_llm, field, None)
            fake_llm.slot_update = SlotSelectionUpdate(action="set", option_number=2)
        elif len(fake_llm.calls) == 2:
            fake_llm.intent = "other"
            fake_llm.slot_update = None
        return original_generate(system, messages)

    monkeypatch.setattr(fake_llm, "generate", respond)
    llm = RecordingLLM(fake_llm)
    service = ConversationService(
        llm,
        "base",
        escalation_service,
        clinic_config,
        clock=fixed_clock,
        availability=availability_factory(clinic_config),
    )
    report = run_case(
        EvalCase(
            id="selection",
            messages=["Complete", "Option 2", "Opening hours?"],
            criteria=["Summarize the exact selected option"],
        ),
        service,
        llm,
    )
    first, selected, faq = report["turns"]
    assert selected["decision"]["slot_update"] == {"action": "set", "option_number": 2}
    assert selected["reply"].startswith("Opção de demonstração selecionada")
    offers = json.loads(selected["system_prompt"].rsplit("\n", 1)[1])["current_workflow_state"]
    snapshot = json.loads(faq["system_prompt"].rsplit("\n", 1)[1])["current_workflow_state"]
    assert snapshot["selected_slot"] == offers["candidate_slots"][1]
    assert snapshot["stage"] == "slot_selected"
    assert fake_llm.calls[2][1][-2] == ChatMessage("assistant", selected["reply"])
    assert "Opções verificadas" in first["reply"]
    json.dumps(report)
