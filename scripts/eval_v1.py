"""Collect live V1 replies for manual review. Not part of pytest; incurs API costs.

Run: uv run python -m scripts.eval_v1
Model and effort use the same Settings/.env as the application.
"""

import argparse
import json
import subprocess
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.clinic_config import load_clinic_config
from app.core.settings import Settings
from app.llm.claude import build_claude_client
from app.llm.client import ChatMessage, EscalationLevel, LLMClient, LLMError, LLMResult
from app.prompts.system_prompt import build_system_prompt
from app.schemas.assistant import AssistantDecision
from app.schemas.chat import ConversationId, MessageText
from app.services.conversation import ConversationService
from app.services.escalation import TEMPLATES_PATH, EscalationService

ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "v1-cases.yaml"


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: ConversationId
    messages: list[MessageText] = Field(min_length=1)
    criteria: list[MessageText] = Field(min_length=1)
    expected_escalation: list[EscalationLevel] | None = None

    @model_validator(mode="after")
    def expected_level_for_each_turn(self) -> "EvalCase":
        if self.expected_escalation is not None and len(self.expected_escalation) != len(
            self.messages
        ):
            raise ValueError("expected_escalation must have one entry per message")
        return self


class EvalSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shared_criteria: list[MessageText] = Field(min_length=1)
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> "EvalSuite":
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Evaluation case ids must be unique")
        return self


class RecordingLLM(LLMClient):
    """Capture provider metadata without changing the application service."""

    def __init__(self, delegate: LLMClient):
        self.delegate = delegate
        self.last_result: LLMResult | None = None

    def generate(self, system: str, messages: Sequence[ChatMessage]) -> LLMResult:
        self.last_result = None
        result = self.delegate.generate(system, messages)
        self.last_result = result
        return result


def run_case(case: EvalCase, service: ConversationService, llm: RecordingLLM) -> dict:
    conversation_id = uuid.uuid4().hex
    turns = []
    execution_status = "completed"
    for index, message in enumerate(case.messages):
        start = time.perf_counter()
        turn = {"message": message}
        try:
            turn["reply"] = service.handle_message(conversation_id, message)
        except LLMError as exc:
            turn["error"] = str(exc)
            execution_status = "error"
        else:
            if llm.last_result is not None:
                # Returned model may differ from the requested model due to fallback.
                result = asdict(llm.last_result)
                turn["usage"] = {
                    key: result[key] for key in ("model", "input_tokens", "output_tokens")
                }
                turn["decision"] = {
                    "escalation": result["escalation"],
                    "language": result["language"],
                }
                if case.expected_escalation is not None:
                    expected = case.expected_escalation[index]
                    turn["escalation_check"] = {
                        "expected": expected,
                        "passed": result["escalation"] == expected,
                    }
        turn["latency_ms"] = round((time.perf_counter() - start) * 1000)
        turns.append(turn)
        if execution_status == "error":
            break  # Later turns depend on the failed reply; continue with the next case.

    return {
        "id": case.id,
        "conversation_id": conversation_id,
        "criteria": case.criteria,
        "execution_status": execution_status,
        "review": {"verdict": "pending", "reason": ""},
        "turns": turns,
    }


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New JSON report path; never overwrite a run")
    parser.add_argument("--cases", type=Path, default=CASES_PATH, help="Scenario YAML to run")
    args = parser.parse_args()
    started_at = datetime.now(UTC)
    output = args.output or (ROOT / "evals" / "results" / f"{started_at:%Y%m%dT%H%M%S%fZ}.json")
    # Validate all cases before constructing a live client or making a paid call.
    suite = EvalSuite.model_validate(yaml.safe_load(args.cases.read_text(encoding="utf-8")))
    settings = Settings()
    config = load_clinic_config()
    system_prompt = build_system_prompt(config)
    llm = RecordingLLM(build_claude_client(settings))
    service = ConversationService(llm, system_prompt, EscalationService(config.emergency))
    report = {
        "started_at": started_at.isoformat(),
        "git_revision": git_output("rev-parse", "HEAD"),
        "git_status": git_output("status", "--short"),
        "settings": {
            "requested_model": settings.llm_model,
            "effort": settings.llm_effort,
            "max_tokens": settings.llm_max_tokens,
            "timeout_seconds": settings.llm_timeout_seconds,
            "max_retries": settings.llm_max_retries,
        },
        "system_prompt": system_prompt,
        "output_schema": AssistantDecision.model_json_schema(),
        "escalation_templates": yaml.safe_load(TEMPLATES_PATH.read_text(encoding="utf-8")),
        "suite": suite.model_dump(),
        "cases": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the path before any paid calls; an existing report is never overwritten.
    with output.open("x", encoding="utf-8") as handle:

        def save_report() -> None:
            handle.seek(0)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.truncate()
            handle.flush()

        save_report()
        print(f"Model: {settings.llm_model}; effort: {settings.llm_effort}", flush=True)
        print(f"Report: {output}", flush=True)
        for case in suite.cases:
            print(f"Running {case.id} ({len(case.messages)} turns)...", flush=True)
            result = run_case(case, service, llm)
            report["cases"].append(result)
            save_report()  # Keep completed cases if a later call is interrupted.
            print(f"  {result['execution_status']}; manual review pending", flush=True)
        report["finished_at"] = datetime.now(UTC).isoformat()
        save_report()
    errors = sum(case["execution_status"] == "error" for case in report["cases"])
    checks = [
        turn["escalation_check"]["passed"]
        for case in report["cases"]
        for turn in case["turns"]
        if "escalation_check" in turn
    ]
    mismatches = len(checks) - sum(checks)
    print(f"Collected {len(report['cases'])} cases; {errors} execution errors. Review required.")
    if checks:
        print(f"Escalation checks matched: {sum(checks)}/{len(checks)}; mismatches: {mismatches}.")
    else:
        print("No expected escalation levels declared; no automatic escalation checks run.")
    return 1 if errors or mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
