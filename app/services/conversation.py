"""Application entry point for a chat message, independent of HTTP or any channel."""

import logging
import time
import uuid

from app.llm.client import ChatMessage, LLMClient, LLMError
from app.services.escalation import EscalationService

logger = logging.getLogger(__name__)


class ConversationService:
    """Keeps each conversation's history and asks the LLM for the next reply.

    History lives in memory, so it is lost on restart (fine for V1; V3 moves it to PostgreSQL).
    """

    def __init__(self, llm: LLMClient, system_prompt: str, escalation: EscalationService):
        self._llm = llm
        self._system_prompt = system_prompt
        self._escalation = escalation
        self._histories: dict[str, list[ChatMessage]] = {}

    def handle_message(self, conversation_id: str, message: str) -> str:
        request_id = uuid.uuid4().hex
        history = self._histories.setdefault(conversation_id, [])
        user_message = ChatMessage("user", message)

        # One log line per message, with ids and numbers only: patient text is
        # personal data (LGPD) and never goes into logs.
        start = time.perf_counter()
        try:
            result = self._llm.generate(self._system_prompt, [*history, user_message])
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
        logger.info(
            "chat request_id=%s conversation_id=%s outcome=ok latency_ms=%d model=%s "
            "input_tokens=%d output_tokens=%d escalation=%s",
            request_id,
            conversation_id,
            _elapsed_ms(start),
            result.model,
            result.input_tokens,
            result.output_tokens,
            result.escalation,
        )

        # Save the turn only after a successful reply: if the call fails, the history
        # never holds a user message without an answer.
        history += [user_message, ChatMessage("assistant", reply)]
        return reply


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
