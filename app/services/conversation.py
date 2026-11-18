"""Application entry point for a chat message, independent of HTTP or any channel."""

import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.core.clinic_config import ClinicConfig, Service
from app.llm.client import ChatMessage, LLMClient, LLMError, ResponseLanguage
from app.prompts.system_prompt import build_turn_prompt
from app.schemas.appointment import TIME_PERIODS, DentistPreference, TimePreference
from app.schemas.conversation import ConversationState
from app.services.escalation import EscalationService
from app.services.preferences import assess_preferences, missing_preference_reply

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
            time_preference = state.time_preference
            conflict_reply = None
            date_reply = None
            time_reply = None
            collection_reply = None
            preference_status = None
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
                time_update = result.time_update
                if time_update is not None:
                    if time_update.action == "set":
                        time_preference = time_update.preference
                    elif time_update.action == "clear":
                        time_preference = None
                if (
                    date_preference is not None
                    and date_preference.start_date == date_preference.end_date == now.date()
                    and time_preference is not None
                    and _time_has_passed(time_preference, now)
                ):
                    time_preference = None
                    time_reply = _past_time_reply(result.language)
                preference_status = assess_preferences(
                    service=self._services.get(service_id),
                    dentist=dentist_preference,
                    dates=date_preference,
                    time=time_preference,
                )
                if preference_status.conflicts:
                    conflict_reply = self._dentist_conflict_reply(
                        self._services[service_id], dentist_preference, result.language
                    )
                # Clear can mean an ambiguous correction; preserve its specific LLM clarification.
                elif not any(
                    proposal is not None and proposal.action == "clear"
                    for proposal in (
                        result.service_update,
                        result.dentist_update,
                        result.date_update,
                        result.time_update,
                    )
                ):
                    service = self._services.get(service_id)
                    collection_reply = missing_preference_reply(
                        preference_status,
                        result.language,
                        eligible_dentists=(
                            [self._dentists[dentist_id].name for dentist_id in service.dentist_ids]
                            if service is not None
                            else []
                        ),
                    )
                    if collection_reply is not None and result.booking_answer:
                        collection_reply = f"{result.booking_answer}\n\n{collection_reply}"
            guidance = self._escalation.reply(result.escalation, result.language)
            reply = (
                guidance
                if guidance is not None
                else date_reply or time_reply or conflict_reply or collection_reply or result.text
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
            state.time_preference = None
        elif result.intent == "booking":
            state.stage = "collecting_preferences"
            state.service_id = service_id
            state.dentist_preference = dentist_preference
            state.date_preference = date_preference
            state.time_preference = time_preference

        logger.info(
            "chat request_id=%s conversation_id=%s outcome=ok latency_ms=%d model=%s "
            "input_tokens=%d output_tokens=%d escalation=%s intent=%s stage=%s "
            "preferences_complete=%s missing_preferences=%s preference_conflicts=%s",
            request_id,
            conversation_id,
            _elapsed_ms(start),
            result.model,
            result.input_tokens,
            result.output_tokens,
            result.escalation,
            result.intent,
            state.stage,
            preference_status.is_complete if preference_status is not None else None,
            ",".join(preference_status.missing) or "none"
            if preference_status is not None
            else None,
            ",".join(preference_status.conflicts) or "none"
            if preference_status is not None
            else None,
        )

        # Save the turn only after a successful reply: if the call fails, the history
        # never holds a user message without an answer.
        state.messages.extend([user_message, ChatMessage("assistant", reply)])
        return reply

    def _dentist_conflict_reply(
        self, service: Service, preference: DentistPreference, language: ResponseLanguage
    ) -> str:
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


def _time_has_passed(preference: TimePreference, now: datetime) -> bool:
    if preference.kind == "window":
        return preference.end_time is not None and preference.end_time < now.time()
    if preference.kind in TIME_PERIODS:
        _, end = TIME_PERIODS[preference.kind]
        return end is not None and end <= now.time()
    return False


def _past_time_reply(language: ResponseLanguage) -> str:
    if language == "pt":
        return (
            "O horário ou período escolhido já passou hoje. "
            "Você prefere um horário mais tarde ou outra data? "
            "Ainda não posso consultar horários nem agendar."
        )
    return (
        "The requested time or period has already passed today. "
        "Would you prefer a later time or another date? "
        "I cannot check availability or book appointments yet."
    )


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
