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
        "Você pode escolher uma opção pelo número ou alterar as preferências."
        if language == "pt"
        else "These options can change; nothing has been reserved or booked. "
        "You can choose an option by number or change your preferences."
    )
    return "\n\n".join([lines[0], "\n".join(lines[1:-1]), lines[-1]])


def selected_slot_reply(
    slot: AppointmentSlot, clinic: ClinicConfig, language: ResponseLanguage
) -> str:
    heading = "Opção de demonstração selecionada" if language == "pt" else "Selected demo option"
    footer = (
        "Você confirma esses dados para agendamento? Responda sim ou não. "
        "Nada foi reservado ou agendado. Esta demonstração registra sua aprovação; "
        "a criação na agenda ainda não está disponível."
        if language == "pt"
        else "Do you confirm these exact details for booking? Please answer yes or no. "
        "Nothing has been reserved or booked. This demo records your approval; "
        "calendar booking is not available yet."
    )
    return f"{heading}:\n\n{_slot_details(slot, clinic)}\n\n{footer}"


def approved_slot_reply(
    slot: AppointmentSlot, clinic: ClinicConfig, language: ResponseLanguage
) -> str:
    heading = (
        "Aprovação registrada para o pedido de demonstração"
        if language == "pt"
        else "Approval recorded for the demo request"
    )
    footer = (
        "Nada foi reservado ou agendado. A criação na agenda ainda não está disponível."
        if language == "pt"
        else "Nothing has been reserved or booked. Calendar booking is not available yet."
    )
    return f"{heading}:\n\n{_slot_details(slot, clinic)}\n\n{footer}"


def _slot_details(slot: AppointmentSlot, clinic: ClinicConfig) -> str:
    service = next(item for item in clinic.services if item.id == slot.service_id)
    dentist = next(item for item in clinic.dentists if item.id == slot.dentist_id)
    zone = ZoneInfo(clinic.timezone)
    start = slot.interval.start.astimezone(zone).isoformat(sep=" ")
    end = slot.interval.end.astimezone(zone).isoformat(sep=" ")
    return f"{service.name} — {dentist.name} — {start} → {end} ({clinic.timezone})"


def invalid_slot_reply(count: int, language: ResponseLanguage) -> str:
    return (
        f"Essa opção não está na lista salva. Escolha um número de 1 a {count}. "
        "Nada foi reservado ou agendado."
        if language == "pt"
        else f"That option is not in the saved list. Choose a number from 1 to {count}. "
        "Nothing has been reserved or booked."
    )


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
