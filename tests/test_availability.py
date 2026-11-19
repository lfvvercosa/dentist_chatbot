"""Slot calculation checks configured clinic rules against deterministic mock calendar reads."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core.clinic_config import TimeRange, Weekday
from app.core.scheduling_config import SchedulingConfig, load_scheduling_config
from app.schemas.appointment import DatePreference, DentistPreference, TimePreference
from app.schemas.calendar import TimeInterval
from app.services.availability import AvailabilityService, SlotSearchError
from app.services.calendar import CalendarError, MockCalendarService

ZONE = ZoneInfo("America/Sao_Paulo")
NOW = datetime(2026, 11, 18, 9, tzinfo=ZONE)


def preference(kind="any", start=None, end=None):
    return TimePreference(kind=kind, start_time=start, end_time=end)


def busy(start="09:00", end="10:00", day="2026-11-19"):
    return TimeInterval(
        start=datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=ZONE),
        end=datetime.fromisoformat(f"{day}T{end}").replace(tzinfo=ZONE),
    )


def local_starts(slots, zone=ZONE):
    return [slot.interval.start.astimezone(zone).time() for slot in slots]


class RecordingCalendar(MockCalendarService):
    def __init__(self, calendars, unavailable_dentists=()):
        super().__init__(calendars, unavailable_dentists=unavailable_dentists)
        self.calls = []

    def get_busy_intervals(self, dentist_id, window):
        self.calls.append((dentist_id, window))
        return super().get_busy_intervals(dentist_id, window)


@pytest.fixture
def settings():
    return load_scheduling_config()


@pytest.fixture
def search():
    return {
        "service_id": "cleaning",
        "dentist": DentistPreference(kind="specific", dentist_id="ana"),
        "dates": DatePreference(start_date="2026-11-19", end_date="2026-11-19"),
        "time_preference": preference(),
        "now": NOW,
    }


@pytest.fixture
def make_availability(clinic_config, settings):
    def make(*, periods=None, unavailable=(), clinic=None, config=None):
        clinic = clinic or clinic_config
        seeds = {dentist.id: [] for dentist in clinic.dentists}
        seeds.update(periods or {})
        calendar = RecordingCalendar(seeds, unavailable)
        return AvailabilityService(clinic, calendar, config or settings), calendar

    return make


def test_demo_settings_are_explicit_and_load_without_model_settings(settings):
    assert settings.model_dump() == {
        "search_horizon_days": 30,
        "slot_spacing_minutes": 15,
        "max_results": 3,
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("search_horizon_days", 0),
        ("search_horizon_days", 91),
        ("slot_spacing_minutes", 0),
        ("slot_spacing_minutes", 1441),
        ("max_results", 0),
        ("max_results", 21),
        ("max_results", True),
        ("max_results", "3"),
        ("extra", 1),
    ],
)
def test_invalid_search_settings_fail_validation(settings, field, value):
    with pytest.raises(ValidationError):
        SchedulingConfig.model_validate(settings.model_dump() | {field: value})


def test_finds_full_duration_slots_skipping_busy_periods_and_allows_touching_edges(
    make_availability, search
):
    service, calendar = make_availability(periods={"ana": [busy()]})
    slots = service.find_slots(**search)
    assert local_starts(slots) == [time(8), time(8, 15), time(10)]
    assert all(slot.interval.end - slot.interval.start == timedelta(minutes=45) for slot in slots)
    assert all(slot.service_id == "cleaning" and slot.dentist_id == "ana" for slot in slots)
    assert all(not slot.interval.overlaps(busy()) for slot in slots)
    assert len(calendar.calls) == 1 and calendar.calls[0][0] == "ana"
    assert all(slot.interval.start.tzinfo is slot.interval.end.tzinfo is UTC for slot in slots)


def test_any_dentist_search_uses_eligibility_and_global_chronological_limit(
    make_availability, search
):
    service, calendar = make_availability()
    search.update(service_id="consultation", dentist=DentistPreference(kind="any"))
    slots = service.find_slots(**search)
    assert [(slot.dentist_id, slot.interval.start.astimezone(ZONE).time()) for slot in slots] == [
        ("ana", time(8)),
        ("bruno", time(8)),
        ("ana", time(8, 15)),
    ]
    assert [dentist for dentist, _ in calendar.calls] == ["ana", "bruno"]


def test_explicit_any_dentist_for_cleaning_reads_only_ana(make_availability, search):
    service, calendar = make_availability()
    search["dentist"] = DentistPreference(kind="any")
    assert service.find_slots(**search)
    assert [dentist for dentist, _ in calendar.calls] == ["ana"]


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"service_id": "private_unknown"}, "unknown service"),
        ({"dentist": DentistPreference()}, "preference is required"),
        (
            {"dentist": DentistPreference(kind="specific", dentist_id="private_unknown")},
            "unknown dentist",
        ),
        ({"dentist": DentistPreference(kind="specific", dentist_id="bruno")}, "does not provide"),
        ({"now": datetime(2026, 11, 18, 9)}, "timezone-aware"),
        (
            {"dates": DatePreference(start_date="2026-11-17", end_date="2026-11-19")},
            "today or later",
        ),
        (
            {"dates": DatePreference(start_date="2026-12-17", end_date="2026-12-18")},
            "search horizon",
        ),
        (
            {"dates": DatePreference(start_date="2027-01-01", end_date="2027-01-01")},
            "search horizon",
        ),
    ],
)
def test_invalid_search_inputs_fail_before_calendar_reads(
    make_availability, search, change, reason
):
    service, calendar = make_availability()
    with pytest.raises(SlotSearchError, match=reason) as error:
        service.find_slots(**(search | change))
    assert calendar.calls == []
    assert "private_unknown" not in str(error.value)


def test_last_horizon_day_is_inclusive_and_range_is_not_silently_clipped(make_availability, search):
    service, _ = make_availability()
    last_day = NOW.date() + timedelta(days=29)
    search["dates"] = DatePreference(start_date=last_day, end_date=last_day)
    assert service.find_slots(**search)
    search["dates"] = DatePreference(start_date=last_day, end_date=last_day + timedelta(days=1))
    with pytest.raises(SlotSearchError, match="horizon"):
        service.find_slots(**search)


def test_future_only_excludes_now_and_retains_later_today_options(make_availability, search):
    service, _ = make_availability()
    search["dates"] = DatePreference(start_date=NOW.date(), end_date=NOW.date())
    assert local_starts(service.find_slots(**search)) == [time(9, 15), time(9, 30), time(9, 45)]


def test_request_clock_is_converted_to_clinic_date(make_availability, search):
    service, _ = make_availability()
    search["now"] = datetime(2026, 11, 19, 1, tzinfo=UTC)  # Still Nov 18 at the clinic.
    search["dates"] = DatePreference(start_date="2026-11-18", end_date="2026-11-18")
    assert service.find_slots(**search) == []  # Clinic closed for the remainder of that day.


def test_saturday_closing_requires_the_full_service_duration(make_availability, search):
    service, _ = make_availability()
    search["dates"] = DatePreference(start_date="2026-11-21", end_date="2026-11-21")
    search["time_preference"] = preference("window", "11:00", None)
    slots = service.find_slots(**search)
    assert local_starts(slots) == [time(11), time(11, 15)]
    assert slots[-1].interval.end.astimezone(ZONE).time() == time(12)


def test_closed_day_returns_no_slots_without_calendar_reads(make_availability, search):
    service, calendar = make_availability()
    search["dates"] = DatePreference(start_date="2026-11-22", end_date="2026-11-22")
    assert service.find_slots(**search) == []
    assert calendar.calls == []


def test_inclusive_date_range_skips_closed_days_and_orders_results_across_days(
    make_availability, search
):
    service, _ = make_availability()
    search["dates"] = DatePreference(start_date="2026-11-21", end_date="2026-11-23")
    search["time_preference"] = preference("window", "11:15", "11:15")
    slots = service.find_slots(**search)
    assert [slot.interval.start.astimezone(ZONE).date() for slot in slots] == [
        date(2026, 11, 21),
        date(2026, 11, 23),
    ]


@pytest.mark.parametrize(
    "kind,starts",
    [
        ("morning", [time(8), time(8, 15), time(8, 30)]),
        ("afternoon", [time(12), time(12, 15), time(12, 30)]),
        ("evening", []),
        ("any", [time(8), time(8, 15), time(8, 30)]),
    ],
)
def test_named_periods_use_existing_demo_meanings(make_availability, search, kind, starts):
    service, _ = make_availability()
    search["time_preference"] = preference(kind)
    assert local_starts(service.find_slots(**search)) == starts


def test_named_period_constrains_start_but_service_may_cross_period_end(make_availability, search):
    service, _ = make_availability(periods={"ana": [busy("08:00", "11:45")]})
    search["time_preference"] = preference("morning")
    slots = service.find_slots(**search)
    assert local_starts(slots) == [time(11, 45)]
    assert slots[0].interval.end.astimezone(ZONE).time() == time(12, 30)


@pytest.mark.parametrize(
    "start,end,starts",
    [
        ("14:00", "14:30", [time(14), time(14, 15), time(14, 30)]),
        ("14:07", None, [time(14, 15), time(14, 30), time(14, 45)]),
        (None, "08:15", [time(8), time(8, 15)]),
        ("08:00:01", "08:15", [time(8, 15)]),
    ],
)
def test_inclusive_windows_and_one_sided_bounds_filter_grid_starts(
    make_availability, search, start, end, starts
):
    service, _ = make_availability()
    search["time_preference"] = preference("window", start, end)
    assert local_starts(service.find_slots(**search)) == starts


@pytest.mark.parametrize(
    "chosen,expected_count", [("14:07", 1), ("17:15", 1), ("17:16", 0), ("07:00", 0)]
)
def test_exact_time_is_checked_without_rounding_or_forcing_the_grid(
    make_availability, search, chosen, expected_count
):
    service, _ = make_availability()
    search["time_preference"] = preference("window", chosen, chosen)
    slots = service.find_slots(**search)
    assert len(slots) == expected_count
    if slots:
        assert local_starts(slots) == [time.fromisoformat(chosen)]


def test_exact_busy_time_returns_no_alternative_substitution(make_availability, search):
    service, _ = make_availability(periods={"ana": [busy("14:00", "15:00")]})
    search["time_preference"] = preference("window", "14:07", "14:07")
    assert service.find_slots(**search) == []


def test_fully_busy_calendar_returns_empty_and_read_failure_stays_distinct(
    make_availability, search
):
    service, _ = make_availability(periods={"ana": [busy("08:00", "18:00")]})
    assert service.find_slots(**search) == []
    unavailable, _ = make_availability(unavailable=["ana"])
    with pytest.raises(CalendarError):
        unavailable.find_slots(**search)


def test_any_dentist_failure_does_not_return_partial_results(make_availability, search):
    service, calendar = make_availability(unavailable=["bruno"])
    search.update(service_id="consultation", dentist=DentistPreference(kind="any"))
    with pytest.raises(CalendarError):
        service.find_slots(**search)
    assert [dentist for dentist, _ in calendar.calls] == ["ana", "bruno"]


def test_configured_spacing_duration_eligibility_and_result_limit_are_used(
    make_availability, clinic_config, settings, search
):
    clinic = clinic_config.model_copy(
        update={
            "services": [
                service.model_copy(update={"duration_minutes": 20, "dentist_ids": ["bruno"]})
                if service.id == "consultation"
                else service
                for service in clinic_config.services
            ]
        }
    )
    config = SchedulingConfig(
        **(settings.model_dump() | {"slot_spacing_minutes": 20, "max_results": 2})
    )
    service, calendar = make_availability(clinic=clinic, config=config)
    search.update(service_id="consultation", dentist=DentistPreference(kind="any"))
    slots = service.find_slots(**search)
    assert local_starts(slots) == [time(8), time(8, 20)]
    assert all(slot.interval.end - slot.interval.start == timedelta(minutes=20) for slot in slots)
    assert [dentist for dentist, _ in calendar.calls] == ["bruno"]


def test_offers_fit_altered_opening_hours(make_availability, clinic_config, search):
    clinic = clinic_config.model_copy(
        update={"opening_hours": {Weekday.THURSDAY: TimeRange(open="10:10", close="11:00")}}
    )
    service, _ = make_availability(clinic=clinic)
    slots = service.find_slots(**search)
    assert local_starts(slots) == [time(10, 10)]
    assert slots[0].interval.end.astimezone(ZONE).time() == time(10, 55)


def test_duration_too_long_for_hours_returns_no_slots_or_reads(
    make_availability, clinic_config, search
):
    clinic = clinic_config.model_copy(
        update={"opening_hours": {Weekday.THURSDAY: TimeRange(open="08:00", close="08:30")}}
    )
    service, calendar = make_availability(clinic=clinic)
    assert service.find_slots(**search) == []
    assert calendar.calls == []


def test_search_does_not_mutate_preferences_or_reserve_an_option(make_availability, search):
    service, calendar = make_availability()
    before = search.copy()
    first = service.find_slots(**search)
    assert service.find_slots(**search) == first
    assert search == before
    assert len(calendar.calls) == 2
    with pytest.raises(ValidationError):
        first[0].dentist_id = "bruno"


@pytest.mark.parametrize(
    "day,chosen,expected_offsets",
    [
        ("2026-03-08", "02:30", []),  # Nonexistent local time is omitted.
        ("2026-11-01", "01:30", [timedelta(hours=-4), timedelta(hours=-5)]),
    ],
)
def test_dst_gap_and_repeated_exact_times_use_real_instants(
    make_availability, clinic_config, search, day, chosen, expected_offsets
):
    zone = ZoneInfo("America/New_York")
    clinic = clinic_config.model_copy(
        update={
            "timezone": str(zone),
            "opening_hours": {Weekday.SUNDAY: TimeRange(open="00:00", close="04:00")},
        }
    )
    service, _ = make_availability(clinic=clinic)
    search.update(
        dates=DatePreference(start_date=day, end_date=day),
        time_preference=preference("window", chosen, chosen),
        now=datetime.fromisoformat(day).replace(tzinfo=zone) - timedelta(days=1),
    )
    slots = service.find_slots(**search)
    assert [slot.interval.start.astimezone(zone).utcoffset() for slot in slots] == expected_offsets
    assert all(slot.interval.end - slot.interval.start == timedelta(minutes=45) for slot in slots)


def test_demo_runs_offline_and_prints_checked_alternatives(capsys):
    from scripts.try_availability import main

    main()
    output = capsys.readouterr().out
    assert "2026-11-19 08:00-08:30" in output
    assert "2026-11-19 10:00-10:30" in output
    assert "2026-11-19 10:15-10:45" in output
    assert "no appointment has been reserved or booked" in output


@pytest.mark.parametrize("day,opening", [("2026-03-08", "02:30"), ("2026-11-01", "01:30")])
def test_nonexistent_or_ambiguous_opening_boundary_fails_before_reads(
    make_availability, clinic_config, search, day, opening
):
    zone = ZoneInfo("America/New_York")
    clinic = clinic_config.model_copy(
        update={
            "timezone": str(zone),
            "opening_hours": {Weekday.SUNDAY: TimeRange(open=opening, close="04:00")},
        }
    )
    service, calendar = make_availability(clinic=clinic)
    search.update(
        dates=DatePreference(start_date=day, end_date=day),
        now=datetime.fromisoformat(day).replace(tzinfo=zone) - timedelta(days=1),
    )
    with pytest.raises(SlotSearchError, match="unambiguous instants"):
        service.find_slots(**search)
    assert calendar.calls == []


def test_offset_bearing_opening_times_are_not_silently_reinterpreted(
    make_availability, clinic_config, search
):
    clinic = clinic_config.model_copy(
        update={
            "opening_hours": {Weekday.THURSDAY: TimeRange(open="08:00:00Z", close="10:00:00Z")},
        }
    )
    service, calendar = make_availability(clinic=clinic)
    with pytest.raises(SlotSearchError, match="without offsets"):
        service.find_slots(**search)
    assert calendar.calls == []
