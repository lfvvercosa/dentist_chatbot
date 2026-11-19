"""Provider-neutral calendar reads and an in-memory implementation for fictional data."""

from abc import ABC, abstractmethod
from collections.abc import Collection, Mapping, Sequence

from app.schemas.calendar import TimeInterval


class CalendarError(Exception):
    """Busy intervals could not be read; this must never be treated as an empty calendar."""


class CalendarService(ABC):
    """Scheduling code reads busy intervals through this contract, independently of a provider."""

    @abstractmethod
    def get_busy_intervals(self, dentist_id: str, window: TimeInterval) -> list[TimeInterval]:
        """Return busy intervals clipped to window, sorted by (start, end), without merging.

        Both the window and results use [start, end) semantics and UTC timestamps.
        Return [] only after a successful read with no overlapping busy intervals.
        Raise CalendarError for an unknown/unmapped dentist or a failed read.
        This read neither checks clinic rules nor reserves or creates an appointment.
        """
        ...


class MockCalendarService(CalendarService):
    """Read a copied snapshot of explicitly registered dentist calendars, without network I/O.

    Use an empty sequence to register a known empty calendar. Seed values must be validated
    TimeInterval objects. unavailable_dentists simulates read failures for registered IDs.
    """

    def __init__(
        self,
        calendars: Mapping[str, Sequence[TimeInterval]],
        *,
        unavailable_dentists: Collection[str] = (),
    ):
        self._busy = {dentist_id: tuple(intervals) for dentist_id, intervals in calendars.items()}
        self._unavailable = frozenset(unavailable_dentists)
        if not self._unavailable.issubset(self._busy):
            raise ValueError("unavailable dentists must have registered mock calendars")

    def get_busy_intervals(self, dentist_id: str, window: TimeInterval) -> list[TimeInterval]:
        if dentist_id not in self._busy:
            raise CalendarError("No calendar is registered for the requested dentist")
        if dentist_id in self._unavailable:
            raise CalendarError("Calendar read is unavailable")
        intervals = [
            TimeInterval(start=max(interval.start, window.start), end=min(interval.end, window.end))
            for interval in self._busy[dentist_id]
            if interval.overlaps(window)
        ]
        return sorted(intervals, key=lambda interval: (interval.start, interval.end))
