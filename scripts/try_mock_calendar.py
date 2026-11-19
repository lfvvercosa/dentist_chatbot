"""Offline calendar-read demo with fictional busy intervals; no model/API credentials needed.

Run from the repo root: uv run python -m scripts.try_mock_calendar
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.clinic_config import load_clinic_config
from app.schemas.calendar import TimeInterval
from app.services.calendar import CalendarError, CalendarService, MockCalendarService


def main() -> None:
    clinic = load_clinic_config()
    timezone = ZoneInfo(clinic.timezone)
    dentist = clinic.dentists[0]

    def interval(start_hour: int, end_hour: int) -> TimeInterval:
        return TimeInterval(
            start=datetime(2026, 11, 19, start_hour, tzinfo=timezone),
            end=datetime(2026, 11, 19, end_hour, tzinfo=timezone),
        )

    calendars = {configured.id: [] for configured in clinic.dentists}
    calendars[dentist.id] = [interval(11, 12), interval(9, 10)]
    calendar: CalendarService = MockCalendarService(calendars)

    print(f"In-memory mock; fictional date 2026-11-19; timezone {clinic.timezone}")
    print(f"Dentist: {dentist.name}")
    print("Busy intervals within 08:00-12:00:")
    for busy in calendar.get_busy_intervals(dentist.id, interval(8, 12)):
        start = busy.start.astimezone(timezone)
        end = busy.end.astimezone(timezone)
        print(f"  {start:%H:%M}-{end:%H:%M}")
    empty_window = calendar.get_busy_intervals(dentist.id, interval(12, 13))
    print(f"Busy intervals within 12:00-13:00: {empty_window}")

    unavailable: CalendarService = MockCalendarService(calendars, unavailable_dentists=[dentist.id])
    try:
        unavailable.get_busy_intervals(dentist.id, interval(8, 12))
    except CalendarError as exc:
        print(f"Simulated read failure: {exc}")


if __name__ == "__main__":
    main()
