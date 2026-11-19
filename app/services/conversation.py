"""Application entry point: interpret a message, run the workflow, then save the accepted turn."""

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.clinic_config import ClinicConfig
from app.llm.client import ChatMessage, LLMClient, LLMError, LLMResult
from app.prompts.system_prompt import build_turn_prompt
from app.schemas.conversation import ConversationState
from app.services.availability import AvailabilityService
from app.services.escalation import EscalationService
from app.services.scheduling import SchedulingWorkflow, TurnOutcome

logger = logging.getLogger(__name__)


class ConversationService:
    """Owns per-conversation state/history in memory; durable storage is planned for V3."""

    def __init__(
        self,
        llm: LLMClient,
        system_prompt: str,
        escalation: EscalationService,
        clinic: ClinicConfig,
        *,
        availability: AvailabilityService,
        clock: Callable[[], datetime] | None = None,
    ):
        self._llm = llm
        self._system_prompt = system_prompt
        self._workflow = SchedulingWorkflow(clinic, escalation, availability)
        self._timezone = ZoneInfo(clinic.timezone)
        self._clock = clock or _utc_now
        self._states: dict[str, ConversationState] = {}

    def handle_message(self, conversation_id: str, message: str) -> str:
        request_id = uuid.uuid4().hex
        state = self._states.setdefault(conversation_id, ConversationState())
        user_message = ChatMessage("user", message)
        start = time.perf_counter()
        try:
            now = self._current_time()
            prompt = build_turn_prompt(self._system_prompt, state, now=now)
            result = self._llm.generate(prompt, [*state.messages, user_message])
            outcome = self._workflow.process(state, result, now)
        except LLMError as exc:
            logger.warning(
                "chat request_id=%s conversation_id=%s outcome=error latency_ms=%d error=%r",
                request_id,
                conversation_id,
                _elapsed_ms(start),
                str(exc),
            )
            raise

        _apply_state(state, outcome.next_state)
        _log_success(request_id, conversation_id, result, outcome, start)
        # Save only the actual successful reply; workflows never edit message history.
        state.messages.extend([user_message, ChatMessage("assistant", outcome.reply)])
        return outcome.reply

    def _current_time(self) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("the conversation clock must return a timezone-aware datetime")
        return now.astimezone(self._timezone)


def _apply_state(state: ConversationState, proposed: ConversationState) -> None:
    """Commit workflow fields in place, preserving the existing state/history object identity."""
    if state is proposed:
        return
    state.stage = proposed.stage
    state.service_id = proposed.service_id
    state.dentist_preference = proposed.dentist_preference
    state.date_preference = proposed.date_preference
    state.time_preference = proposed.time_preference
    state.candidate_slots = proposed.candidate_slots
    state.slots_checked_at = proposed.slots_checked_at


def _log_success(
    request_id: str, conversation_id: str, result: LLMResult, outcome: TurnOutcome, start: float
) -> None:
    # Fixed metadata only: patient text, clinic names, and provider details stay out of logs.
    status = outcome.preference_status
    logger.info(
        "chat request_id=%s conversation_id=%s outcome=ok latency_ms=%d model=%s "
        "input_tokens=%d output_tokens=%d escalation=%s intent=%s stage=%s "
        "preferences_complete=%s missing_preferences=%s preference_conflicts=%s "
        "availability=%s offered_slots=%d",
        request_id,
        conversation_id,
        _elapsed_ms(start),
        result.model,
        result.input_tokens,
        result.output_tokens,
        result.escalation,
        result.intent,
        outcome.next_state.stage,
        status.is_complete if status is not None else None,
        (",".join(status.missing) or "none") if status is not None else None,
        (",".join(status.conflicts) or "none") if status is not None else None,
        outcome.availability_outcome,
        outcome.offered_slots,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
