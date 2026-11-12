"""Application entry point for a chat message, independent of HTTP or any channel."""

import logging
import time
import uuid

from app.llm.client import ChatMessage, LLMClient, LLMError
from app.schemas.conversation import ConversationState
from app.services.escalation import EscalationService

logger = logging.getLogger(__name__)


class ConversationService:
    """Keeps each conversation's state and asks the LLM for the next reply.

    State lives in memory and is lost on restart; durable storage is planned for V3.
    """

    def __init__(self, llm: LLMClient, system_prompt: str, escalation: EscalationService):
        self._llm = llm
        self._system_prompt = system_prompt
        self._escalation = escalation
        self._states: dict[str, ConversationState] = {}

    def handle_message(self, conversation_id: str, message: str) -> str:
        request_id = uuid.uuid4().hex
        state = self._states.setdefault(conversation_id, ConversationState())
        user_message = ChatMessage("user", message)

        # One log line per message, with ids and numbers only: patient text is
        # personal data (LGPD) and never goes into logs.
        start = time.perf_counter()
        try:
            result = self._llm.generate(self._system_prompt, [*state.messages, user_message])
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
        elif result.intent == "booking":
            state.stage = "collecting_preferences"

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
