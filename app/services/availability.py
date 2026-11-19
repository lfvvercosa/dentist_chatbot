"""Calculate candidate appointments from clinic rules and the CalendarService read contract."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.core.clinic_config import ClinicConfig, Weekday
from app.core.scheduling_config import SchedulingConfig
from app.schemas.appointment import (
    TIME_PERIODS,
    AppointmentSlot,
    DatePreference,
    DentistPreference,
    TimePreference,
)
from app.schemas.calendar import TimeInterval
from app.services.calendar import CalendarService


class SlotSearchError(ValueError):
    """Invalid search inputs or unsupported clinic time boundaries, before calendar reads."""

    def __init__(self, message: str, *, allowed_dates: DatePreference | None = None):
        super().__init__(message)
        self.allowed_dates = allowed_dates


class AvailabilityService:
    def __init__(self, clinic: ClinicConfig, calendar: CalendarService, settings: SchedulingConfig):
        self._clinic = clinic
        self._calendar = calendar
        self._settings = settings
        self._timezone = ZoneInfo(clinic.timezone)
        self._services = {service.id: service for service in clinic.services}
        self._dentist_ids = {dentist.id for dentist in clinic.dentists}

    def find_slots(
        self,
        *,
        service_id: str,
        dentist: DentistPreference,
        dates: DatePreference,
        time_preference: TimePreference,
        now: datetime,
    ) -> list[AppointmentSlot]:
        """Return a bounded list of checked alternatives; propagate CalendarError on read failure.

        Dates must fit wholly inside the configured horizon. Time preferences constrain
        appointment starts; the full elapsed duration must fit within clinic opening hours.
        The caller supplies its one captured request clock. No draft or calendar is changed.
        """
        if now.utcoffset() is None:
            raise SlotSearchError("slot search requires a timezone-aware clock")
        today = now.astimezone(self._timezone).date()
        now = now.astimezone(UTC)
        if dates.start_date < today:
            raise SlotSearchError("requested dates must start today or later")
        if dates.end_date.toordinal() >= today.toordinal() + self._settings.search_horizon_days:
            last_date = date.fromordinal(
                min(
                    today.toordinal() + self._settings.search_horizon_days - 1, date.max.toordinal()
                )
            )
            raise SlotSearchError(
                "requested dates exceed the configured search horizon",
                allowed_dates=DatePreference(start_date=today, end_date=last_date),
            )
        service = self._services.get(service_id)
        if service is None:
            raise SlotSearchError("unknown service")
        if dentist.kind == "unspecified":
            raise SlotSearchError("a specific or explicit any dentist preference is required")
        if dentist.kind == "specific":
            if dentist.dentist_id not in self._dentist_ids:
                raise SlotSearchError("unknown dentist")
            if dentist.dentist_id not in service.dentist_ids:
                raise SlotSearchError("the requested dentist does not provide this service")
            dentist_ids = [dentist.dentist_id]
        else:
            dentist_ids = sorted(service.dentist_ids)

        candidates = self._candidate_intervals(
            dates, time_preference, service.duration_minutes, now
        )
        if not candidates:
            return []
        window = TimeInterval(start=candidates[0].start, end=max(item.end for item in candidates))
        # Read every eligible dentist first, so failures cannot yield partial offers.
        busy = {
            dentist_id: self._calendar.get_busy_intervals(dentist_id, window)
            for dentist_id in dentist_ids
        }
        slots = []
        for candidate in candidates:
            for dentist_id in dentist_ids:
                if not any(candidate.overlaps(blocked) for blocked in busy[dentist_id]):
                    slots.append(
                        AppointmentSlot(
                            service_id=service_id, dentist_id=dentist_id, interval=candidate
                        )
                    )
                    if len(slots) == self._settings.max_results:
                        return slots
        return slots

    def _candidate_intervals(
        self,
        dates: DatePreference,
        preference: TimePreference,
        duration_minutes: int,
        now: datetime,
    ) -> list[TimeInterval]:
        duration = timedelta(minutes=duration_minutes)
        spacing = timedelta(minutes=self._settings.slot_spacing_minutes)
        exact = preference.kind == "window" and preference.start_time == preference.end_time
        candidates = []
        for offset in range((dates.end_date - dates.start_date).days + 1):
            day = dates.start_date + timedelta(days=offset)
            hours = self._clinic.opening_hours.get(tuple(Weekday)[day.weekday()])
            if hours is None:
                continue
            openings = self._local_instants(day, hours.open)
            closings = self._local_instants(day, hours.close)
            if len(openings) != 1 or len(closings) != 1:
                raise SlotSearchError(
                    "clinic opening boundaries must identify unambiguous instants"
                )
            opening, closing = openings[0], closings[0]
            if exact:
                starts = self._local_instants(day, preference.start_time)
            else:
                starts = []
                start = opening
                while start + duration <= closing:
                    starts.append(start)
                    start += spacing
            for start in starts:
                if (
                    start > now
                    and start >= opening
                    and start + duration <= closing
                    and _matches_time(start.astimezone(self._timezone).time(), preference)
                ):
                    candidates.append(TimeInterval(start=start, end=start + duration))
        return sorted(candidates, key=lambda item: item.start)

    def _local_instants(self, day: date, local_time: time) -> list[datetime]:
        """Round-trip wall times to omit nonexistent times and retain both repeated instants."""
        if local_time.tzinfo is not None:
            raise SlotSearchError("clinic and preference times must be local times without offsets")
        wall_time = datetime.combine(day, local_time)
        instants = set()
        for fold in (0, 1):
            instant = wall_time.replace(tzinfo=self._timezone, fold=fold).astimezone(UTC)
            if instant.astimezone(self._timezone).replace(tzinfo=None) == wall_time:
                instants.add(instant)
        return sorted(instants)


def _matches_time(value: time, preference: TimePreference) -> bool:
    if preference.kind == "any":
        return True
    if preference.kind in TIME_PERIODS:
        start, end = TIME_PERIODS[preference.kind]
        return value >= start and (end is None or value < end)
    return (preference.start_time is None or value >= preference.start_time) and (
        preference.end_time is None or value <= preference.end_time
    )
