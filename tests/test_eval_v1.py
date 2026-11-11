"""Evaluation plumbing tests use FakeLLM; importing the runner makes no live calls."""

import pytest
from pydantic import ValidationError

from app.llm.client import ChatMessage, LLMError
from app.services.conversation import ConversationService
from scripts.eval_v1 import EvalCase, EvalSuite, RecordingLLM, run_case


def test_eval_preserves_history_but_isolates_cases_and_leaves_grading_pending(
    fake_llm, escalation_service
):
    llm = RecordingLLM(fake_llm)
    service = ConversationService(llm, "real prompt placeholder", escalation_service)
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
    fake_llm, escalation_service
):
    llm = RecordingLLM(fake_llm)
    service = ConversationService(llm, "prompt", escalation_service)
    case = EvalCase(id="booking", messages=["Book", "Confirm"], criteria=["Cannot book"])
    fake_llm.error = LLMError("API unavailable")

    result = run_case(case, service, llm)

    assert len(fake_llm.calls) == 1
    assert result["execution_status"] == "error"
    assert result["review"]["verdict"] == "pending"
    assert result["turns"][0]["error"] == "API unavailable"
    assert "usage" not in result["turns"][0]

    fake_llm.error = None
    assert run_case(case, service, llm)["execution_status"] == "completed"


def test_duplicate_case_ids_are_rejected_before_a_run():
    case = EvalCase(id="same", messages=["Hello"], criteria=["Respond"])
    with pytest.raises(ValidationError, match="ids must be unique"):
        EvalSuite(shared_criteria=["No invented facts"], cases=[case, case])


def test_escalation_check_compares_the_model_decision_with_expected_level(
    fake_llm, escalation_service
):
    llm = RecordingLLM(fake_llm)
    service = ConversationService(llm, "prompt", escalation_service)
    case = EvalCase(
        id="urgent", messages=["Help"], criteria=["Escalate"], expected_escalation=["urgent"]
    )

    mismatch = run_case(case, service, llm)
    assert mismatch["turns"][0]["escalation_check"] == {"expected": "urgent", "passed": False}

    fake_llm.escalation = "urgent"
    match = run_case(case, service, llm)
    assert match["turns"][0]["escalation_check"]["passed"] is True
    assert match["turns"][0]["decision"] == {"escalation": "urgent", "language": "pt"}
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
