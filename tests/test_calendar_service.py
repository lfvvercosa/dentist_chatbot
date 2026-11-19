"""Calendar interval and mock-read behavior is deterministic and requires no network."""

from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.schemas.calendar import TimeInterval
from app.services.calendar import CalendarError, CalendarService, MockCalendarService


def interval(start: int, end: int) -> TimeInterval:
    return TimeInterval(
        start=datetime(2026, 11, 19, start, tzinfo=UTC),
        end=datetime(2026, 11, 19, end, tzinfo=UTC),
    )


def test_interval_normalizes_aware_values_and_json_to_utc():
    clinic_time = datetime(2026, 11, 19, 9, tzinfo=ZoneInfo("America/Sao_Paulo"))
    result = TimeInterval(start=clinic_time, end="2026-11-19T10:00:00-03:00")
    assert result.start == datetime(2026, 11, 19, 12, tzinfo=UTC)
    assert result.end == datetime(2026, 11, 19, 13, tzinfo=UTC)
    assert result.start.tzinfo is result.end.tzinfo is UTC
    assert result.model_dump(mode="json") == {
        "start": "2026-11-19T12:00:00Z",
        "end": "2026-11-19T13:00:00Z",
    }
    assert TimeInterval.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("field", ["start", "end"])
@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 11, 19, 9),
        "2026-11-19T09:00:00",
        "2026-11-19",
        date(2026, 11, 19),
        1795078800,
        True,
        "not a datetime",
    ],
)
def test_interval_rejects_naive_dates_and_non_datetime_values(field, value):
    values = {"start": "2026-11-19T09:00:00Z", "end": "2026-11-19T10:00:00Z"}
    values[field] = value
    with pytest.raises(ValidationError):
        TimeInterval.model_validate(values)


@pytest.mark.parametrize("start,end", [(9, 9), (10, 9)])
def test_interval_rejects_empty_and_reversed_ranges(start, end):
    with pytest.raises(ValidationError, match="start must be before end"):
        interval(start, end)


def test_interval_order_compares_instants_across_offsets():
    result = TimeInterval(start="2026-11-19T10:00:00+02:00", end="2026-11-19T09:00:00Z")
    assert result == interval(8, 9)
    with pytest.raises(ValidationError, match="start must be before end"):
        TimeInterval(start="2026-11-19T08:00:00-03:00", end="2026-11-19T10:00:00Z")


def test_interval_order_handles_the_repeated_hour_at_a_dst_transition():
    zone = ZoneInfo("America/New_York")
    repeated_hour = datetime(2026, 11, 1, 1, 30, tzinfo=zone)
    result = TimeInterval(start=repeated_hour, end=repeated_hour.replace(fold=1))
    assert result.end - result.start == timedelta(hours=1)


def test_interval_rejects_extra_fields_and_is_immutable():
    with pytest.raises(ValidationError):
        TimeInterval(start="2026-11-19T09:00:00Z", end="2026-11-19T10:00:00Z", extra=True)
    value = interval(9, 10)
    with pytest.raises(ValidationError):
        value.start = datetime(2026, 11, 19, 8, tzinfo=UTC)


@pytest.mark.parametrize(
    "other,expected",
    [
        (interval(8, 9), False),
        (interval(10, 11), False),
        (interval(8, 10), True),
        (interval(9, 10), True),
        (interval(9, 11), True),
    ],
)
def test_interval_overlap_excludes_touching_endpoints(other, expected):
    assert interval(9, 10).overlaps(other) is expected
    assert other.overlaps(interval(9, 10)) is expected


def test_calendar_contract_requires_an_implementation():
    with pytest.raises(TypeError):
        CalendarService()


def test_mock_sorts_and_clips_busy_intervals_and_excludes_nonoverlap():
    calendar: CalendarService = MockCalendarService(
        {
            "ana": [
                interval(15, 16),
                interval(10, 12),
                interval(8, 10),
                interval(7, 8),
                interval(12, 13),
            ]
        }
    )
    assert calendar.get_busy_intervals("ana", interval(9, 11)) == [
        interval(9, 10),
        interval(10, 11),
    ]


def test_mock_sorts_after_clipping_and_preserves_overlapping_intervals():
    calendar = MockCalendarService({"ana": [interval(8, 13), interval(7, 15), interval(10, 11)]})
    assert calendar.get_busy_intervals("ana", interval(9, 14)) == [
        interval(9, 13),
        interval(9, 14),
        interval(10, 11),
    ]


def test_busy_interval_covering_query_clips_to_entire_query():
    calendar = MockCalendarService({"ana": [interval(0, 23)]})
    assert calendar.get_busy_intervals("ana", interval(8, 12)) == [interval(8, 12)]


@pytest.mark.parametrize("window", [interval(8, 9), interval(10, 11)])
def test_mock_excludes_busy_intervals_touching_query_edges(window):
    calendar = MockCalendarService({"ana": [interval(9, 10)]})
    assert calendar.get_busy_intervals("ana", window) == []


def test_mock_distinguishes_known_empty_outside_window_and_unmapped_dentist():
    calendar = MockCalendarService({"ana": [interval(9, 10)], "bruno": []})
    assert calendar.get_busy_intervals("bruno", interval(8, 12)) == []
    assert calendar.get_busy_intervals("ana", interval(12, 13)) == []
    with pytest.raises(CalendarError, match="No calendar is registered") as error:
        calendar.get_busy_intervals("private_unknown", interval(8, 12))
    assert "private_unknown" not in str(error.value)


def test_mock_read_failure_is_an_error_even_for_an_empty_calendar():
    calendar = MockCalendarService({"ana": [], "bruno": []}, unavailable_dentists=["ana"])
    with pytest.raises(CalendarError, match="unavailable"):
        calendar.get_busy_intervals("ana", interval(8, 12))
    assert calendar.get_busy_intervals("bruno", interval(8, 12)) == []


def test_mock_rejects_failure_configuration_for_an_unregistered_dentist():
    with pytest.raises(ValueError, match="registered mock calendars"):
        MockCalendarService({"ana": []}, unavailable_dentists=["unknown"])


def test_mock_keeps_dentist_calendars_separate():
    calendar = MockCalendarService({"ana": [interval(9, 10)], "bruno": [interval(10, 11)]})
    assert calendar.get_busy_intervals("ana", interval(8, 12)) == [interval(9, 10)]
    assert calendar.get_busy_intervals("bruno", interval(8, 12)) == [interval(10, 11)]


def test_mock_copies_seed_data_and_returns_independent_result_lists():
    seed = {"ana": [interval(9, 10)]}
    calendar = MockCalendarService(seed)
    seed["ana"].append(interval(10, 11))
    seed["bruno"] = []
    first = calendar.get_busy_intervals("ana", interval(8, 12))
    first.clear()
    assert calendar.get_busy_intervals("ana", interval(8, 12)) == [interval(9, 10)]
    with pytest.raises(CalendarError):
        calendar.get_busy_intervals("bruno", interval(8, 12))


def test_mock_matches_the_same_instants_in_different_timezones():
    seed = TimeInterval(start="2026-11-19T09:00:00-03:00", end="2026-11-19T10:00:00-03:00")
    query = TimeInterval(
        start=datetime(2026, 11, 19, 13, tzinfo=timezone(timedelta(hours=2))),
        end=datetime(2026, 11, 19, 15, tzinfo=timezone(timedelta(hours=2))),
    )
    assert MockCalendarService({"ana": [seed]}).get_busy_intervals("ana", query) == [
        interval(12, 13)
    ]


def test_demo_is_offline_and_shows_busy_empty_and_failure_cases(capsys):
    from scripts.try_mock_calendar import main

    main()
    output = capsys.readouterr().out
    assert "09:00-10:00" in output and "11:00-12:00" in output
    assert "Busy intervals within 12:00-13:00: []" in output
    assert "Simulated read failure: Calendar read is unavailable" in output
