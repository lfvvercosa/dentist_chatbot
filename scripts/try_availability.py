"""Offline slot calculation with fictional busy periods; no chat, model, or network.

Run from the repo root: uv run python -m scripts.try_availability
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.clinic_config import load_clinic_config
from app.core.scheduling_config import load_scheduling_config
from app.schemas.appointment import DatePreference, DentistPreference, TimePreference
from app.schemas.calendar import TimeInterval
from app.services.availability import AvailabilityService
from app.services.calendar import MockCalendarService


def main() -> None:
    clinic = load_clinic_config()
    settings = load_scheduling_config()
    timezone = ZoneInfo(clinic.timezone)
    service = clinic.services[0]
    dentist_id = service.dentist_ids[0]
    dentist = next(item for item in clinic.dentists if item.id == dentist_id)
    calendars = {item.id: [] for item in clinic.dentists}
    calendars[dentist_id] = [
        TimeInterval(
            start=datetime(2026, 11, 19, 8, 30, tzinfo=timezone),
            end=datetime(2026, 11, 19, 10, tzinfo=timezone),
        )
    ]
    availability = AvailabilityService(clinic, MockCalendarService(calendars), settings)
    slots = availability.find_slots(
        service_id=service.id,
        dentist=DentistPreference(kind="specific", dentist_id=dentist_id),
        dates=DatePreference(start_date="2026-11-19", end_date="2026-11-19"),
        time_preference=TimePreference(kind="morning", start_time=None, end_time=None),
        now=datetime(2026, 11, 18, 9, tzinfo=timezone),
    )
    print(f"Mock candidates; fictional date 2026-11-19; timezone {clinic.timezone}")
    print(f"Service: {service.name} ({service.duration_minutes} minutes); dentist: {dentist.name}")
    print("Fictional busy period: 08:30-10:00")
    print(
        f"Search horizon: {settings.search_horizon_days} dates; "
        f"spacing: {settings.slot_spacing_minutes} minutes; limit: {settings.max_results}"
    )
    for slot in slots:
        start = slot.interval.start.astimezone(timezone)
        end = slot.interval.end.astimezone(timezone)
        print(f"  {start:%Y-%m-%d %H:%M}-{end:%H:%M}")
    print("These are alternatives from the mock; no appointment has been reserved or booked.")


if __name__ == "__main__":
    main()
