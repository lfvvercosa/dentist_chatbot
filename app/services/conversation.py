"""Application entry point for a chat message, independent of HTTP or any channel."""

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.clinic_config import ClinicConfig
from app.llm.client import ChatMessage, LLMClient, LLMError, ResponseLanguage
from app.prompts.system_prompt import build_turn_prompt
from app.schemas.appointment import DentistPreference
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
        *,
        clock: Callable[[], datetime] | None = None,
    ):
        self._llm = llm
        self._system_prompt = system_prompt
        self._escalation = escalation
        self._services = {service.id: service for service in clinic.services}
        self._dentists = {dentist.id: dentist for dentist in clinic.dentists}
        self._timezone = ZoneInfo(clinic.timezone)
        self._clock = clock or _utc_now
        self._states: dict[str, ConversationState] = {}

    def handle_message(self, conversation_id: str, message: str) -> str:
        request_id = uuid.uuid4().hex
        state = self._states.setdefault(conversation_id, ConversationState())
        user_message = ChatMessage("user", message)

        # One log line per message, with ids and numbers only: patient text is
        # personal data (LGPD) and never goes into logs.
        start = time.perf_counter()
        try:
            now = self._clock()
            if now.utcoffset() is None:
                raise ValueError("the conversation clock must return a timezone-aware datetime")
            now = now.astimezone(self._timezone)
            turn_prompt = build_turn_prompt(self._system_prompt, state, now=now)
            result = self._llm.generate(turn_prompt, [*state.messages, user_message])
            # Validate proposals before changing state or saving this turn.
            service_id = state.service_id
            dentist_preference = state.dentist_preference
            date_preference = state.date_preference
            conflict_reply = None
            date_reply = None
            if result.escalation == "none" and result.intent == "booking":
                update = result.service_update
                if update is not None and update.action == "set":
                    if update.service_id not in self._services:
                        raise LLMError("Claude proposed an unknown service ID")
                    service_id = update.service_id
                elif update is not None and update.action == "clear":
                    service_id = None
                dentist_update = result.dentist_update
                if dentist_update is not None:
                    if dentist_update.action == "set":
                        if dentist_update.dentist_id not in self._dentists:
                            raise LLMError("Claude proposed an unknown dentist ID")
                        dentist_preference = DentistPreference(
                            kind="specific", dentist_id=dentist_update.dentist_id
                        )
                    elif dentist_update.action == "any":
                        dentist_preference = DentistPreference(kind="any")
                    elif dentist_update.action == "clear":
                        dentist_preference = DentistPreference()
                date_update = result.date_update
                if date_update is not None:
                    if date_update.action == "set":
                        date_preference = date_update.date_range
                    elif date_update.action == "clear":
                        date_preference = None
                if date_preference is not None and date_preference.start_date < now.date():
                    date_preference = None
                    date_reply = _past_date_reply(now.date(), result.language)
                conflict_reply = self._dentist_conflict_reply(
                    service_id, dentist_preference, result.language
                )
            guidance = self._escalation.reply(result.escalation, result.language)
            reply = (
                guidance if guidance is not None else date_reply or conflict_reply or result.text
            )
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
            state.dentist_preference = DentistPreference()
            state.date_preference = None
        elif result.intent == "booking":
            state.stage = "collecting_preferences"
            state.service_id = service_id
            state.dentist_preference = dentist_preference
            state.date_preference = date_preference

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

    def _dentist_conflict_reply(
        self, service_id: str | None, preference: DentistPreference, language: ResponseLanguage
    ) -> str | None:
        if service_id is None or preference.kind != "specific":
            return None
        service = self._services[service_id]
        if preference.dentist_id in service.dentist_ids:
            return None
        dentist = self._dentists[preference.dentist_id]
        eligible = ", ".join(self._dentists[dentist_id].name for dentist_id in service.dentist_ids)
        if language == "pt":
            return (
                f"{dentist.name} não oferece o serviço {service.name}. "
                f"Para esse serviço, você pode escolher: {eligible}. "
                "Você prefere mudar o profissional ou o serviço? "
                "Ainda não posso consultar horários nem agendar."
            )
        return (
            f"{dentist.name} does not provide {service.name}. "
            f"For this service, you can choose: {eligible}. "
            "Would you prefer to change the dentist or the service? "
            "I cannot check availability or book appointments yet."
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _past_date_reply(today: date, language: ResponseLanguage) -> str:
    if language == "pt":
        return (
            "A data ou o início do intervalo informado já passou. "
            f"Pode informar uma data ou intervalo a partir de {today.isoformat()}? "
            "Ainda não posso consultar horários nem agendar."
        )
    return (
        "The date or the start of the requested range is in the past. "
        f"Could you choose a date or range starting on or after {today.isoformat()}? "
        "I cannot check availability or book appointments yet."
    )


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
