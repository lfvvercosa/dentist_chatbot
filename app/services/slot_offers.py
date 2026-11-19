"""Render checked demo-calendar outcomes from backend values, independently of model prose."""

from zoneinfo import ZoneInfo

from app.core.clinic_config import ClinicConfig
from app.llm.client import ResponseLanguage
from app.schemas.appointment import AppointmentSlot
from app.services.availability import SlotSearchError


def slot_offer_reply(
    slots: list[AppointmentSlot], clinic: ClinicConfig, language: ResponseLanguage
) -> str:
    if not slots:
        return (
            "Não encontrei horários na agenda de demonstração para essas preferências. "
            "Você prefere mudar a data, o horário ou o profissional? Nada foi agendado."
            if language == "pt"
            else "I found no matching slots in the demo calendar for these preferences. "
            "Would you like to change the date, time, or dentist? Nothing has been booked."
        )
    services = {service.id: service.name for service in clinic.services}
    dentists = {dentist.id: dentist.name for dentist in clinic.dentists}
    zone = ZoneInfo(clinic.timezone)
    lines = [
        f"Opções verificadas na agenda de demonstração ({clinic.timezone}):"
        if language == "pt"
        else f"Checked demo-calendar options ({clinic.timezone}):"
    ]
    for index, slot in enumerate(slots, 1):
        start = slot.interval.start.astimezone(zone).isoformat(sep=" ")
        end = slot.interval.end.astimezone(zone).isoformat(sep=" ")
        lines.append(
            f"{index}. {services[slot.service_id]} — {dentists[slot.dentist_id]} — {start} → {end}"
        )
    lines.append(
        "Essas opções podem mudar; nada foi reservado ou agendado. "
        "Você pode alterar as preferências. A escolha de opções ainda não está disponível."
        if language == "pt"
        else "These options can change; nothing has been reserved or booked. "
        "You can change your preferences. Option selection is not available yet."
    )
    return "\n\n".join([lines[0], "\n".join(lines[1:-1]), lines[-1]])


def slot_search_error_reply(error: SlotSearchError, language: ResponseLanguage) -> str:
    if error.allowed_dates is not None:
        start = error.allowed_dates.start_date.isoformat()
        end = error.allowed_dates.end_date.isoformat()
        return (
            f"A busca de demonstração aceita datas de {start} a {end}, inclusive. "
            "Pode informar uma data ou intervalo dentro desses limites? Nada foi agendado."
            if language == "pt"
            else f"The demo search accepts dates from {start} through {end}, inclusive. "
            "Could you choose a date or range within those limits? Nothing has been booked."
        )
    return (
        "Não consegui pesquisar horários para essas preferências. "
        "Revise as escolhas ou entre em contato com a clínica. Nada foi agendado."
        if language == "pt"
        else "I could not search for slots with these preferences. "
        "Please revise your choices or contact the clinic. Nothing has been booked."
    )


def calendar_read_error_reply(language: ResponseLanguage) -> str:
    return (
        "Não foi possível consultar a agenda de demonstração agora. "
        "Tente novamente; não tenho horários verificados para oferecer. Nada foi agendado."
        if language == "pt"
        else "The demo calendar could not be read right now. "
        "Please try again; I have no checked slots to offer. Nothing has been booked."
    )
