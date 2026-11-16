"""Application entry point for a chat message, independent of HTTP or any channel."""

import logging
import time
import uuid

from app.core.clinic_config import ClinicConfig
from app.llm.client import ChatMessage, LLMClient, LLMError
from app.prompts.system_prompt import build_turn_prompt
from app.schemas.conversation import ConversationState
from app.services.escalation import EscalationService

logger = logging.getLogger(__name__)


class ConversationService:
    """Keeps each conversation's state and asks the LLM for the next reply.

    State lives in memory and is lost on restart; durable storage is planned for V3.
    """

    def __init__(
        self,
        llm: LLMClient,
        system_prompt: str,
        escalation: EscalationService,
        clinic: ClinicConfig,
    ):
        self._llm = llm
        self._system_prompt = system_prompt
        self._escalation = escalation
        self._service_ids = {service.id for service in clinic.services}
        self._states: dict[str, ConversationState] = {}

    def handle_message(self, conversation_id: str, message: str) -> str:
        request_id = uuid.uuid4().hex
        state = self._states.setdefault(conversation_id, ConversationState())
        user_message = ChatMessage("user", message)

        # One log line per message, with ids and numbers only: patient text is
        # personal data (LGPD) and never goes into logs.
        start = time.perf_counter()
        try:
            turn_prompt = build_turn_prompt(self._system_prompt, state)
            result = self._llm.generate(turn_prompt, [*state.messages, user_message])
            # Validate the proposed service before changing state or saving this turn.
            service_id = state.service_id
            if result.escalation == "none" and result.intent == "booking":
                update = result.service_update
                if update is not None and update.action == "set":
                    if update.service_id not in self._service_ids:
                        raise LLMError("Claude proposed an unknown service ID")
                    service_id = update.service_id
                elif update is not None and update.action == "clear":
                    service_id = None
            guidance = self._escalation.reply(result.escalation, result.language)
            reply = guidance if guidance is not None else result.text
        except LLMError as exc:
            logger.warning(
                "chat request_id=%s conversation_id=%s outcome=error latency_ms=%d error=%r",
                request_id,
                conversation_id,
                _elapsed_ms(start),
                str(exc),
            )
            raise

        # Python owns transitions; an unrelated question preserves the booking draft.
        # Escalation takes priority, even when the same message asks for an appointment.
        if result.escalation != "none":
            state.stage = "chatting"
            state.service_id = None
        elif result.intent == "booking":
            state.stage = "collecting_preferences"
            state.service_id = service_id

        logger.info(
            "chat request_id=%s conversation_id=%s outcome=ok latency_ms=%d model=%s "
            "input_tokens=%d output_tokens=%d escalation=%s intent=%s stage=%s",
            request_id,
            conversation_id,
            _elapsed_ms(start),
            result.model,
            result.input_tokens,
            result.output_tokens,
            result.escalation,
            result.intent,
            state.stage,
        )

        # Save the turn only after a successful reply: if the call fails, the history
        # never holds a user message without an answer.
        state.messages.extend([user_message, ChatMessage("assistant", reply)])
        return reply


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
