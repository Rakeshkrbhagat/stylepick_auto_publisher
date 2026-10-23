from datetime import datetime, timedelta, timezone


import pytest

from src import scheduling as s


def test_example_preserves_time():
    o = s.parse_original("9 September", "6:30 PM", default_year=2026)
    assert s.propose(o) == datetime(2026, 9, 10, 18, 30)


@pytest.mark.parametrize("orig,expected", [
    (datetime(2026, 1, 31, 8, 5), datetime(2026, 2, 1, 8, 5)),          # month end
    (datetime(2026, 4, 30, 9, 0), datetime(2026, 5, 1, 9, 0)),
    (datetime(2026, 12, 31, 23, 59), datetime(2027, 1, 1, 23, 59)),     # year end
    (datetime(2024, 2, 28, 10), datetime(2024, 2, 29, 10)),             # leap year
    (datetime(2024, 2, 29, 10), datetime(2024, 3, 1, 10)),
    (datetime(2026, 2, 28, 10), datetime(2026, 3, 1, 10)),              # non-leap
])
def test_boundaries(orig, expected):
    assert s.propose(orig) == expected


def test_time_and_tz_preserved_across_dst():
    tz = s.get_tz("America/New_York")
    p = s.propose(datetime(2026, 3, 7, 18, 30, tzinfo=tz))  # DST starts 8 March
    assert (p.day, p.hour, p.minute, p.tzinfo) == (8, 18, 30, tz)
    ist = timezone(timedelta(hours=5, minutes=30))
    assert s.propose(datetime(2026, 9, 9, 10, 30, tzinfo=ist)).tzinfo == ist


def test_to_local_uses_user_timezone():
    utc = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)
    assert s.to_local(utc, "Asia/Kolkata").day == 10  # 01:30 next day in IST


def test_parse_formats_and_errors():
    assert s.parse_original("2026-09-09 22:15") == datetime(2026, 9, 9, 22, 15)
    assert s.parse_original("September 9th, 2026", "10:30 am") == datetime(2026, 9, 9, 10, 30)
    for bad in (("", ""), ("31 February 2026", "10:00"), ("9 September", "")):
        with pytest.raises(s.ScheduleParseError):
            s.parse_original(*bad)
    with pytest.raises(s.ScheduleParseError):
        s.get_tz("Mars/Base")


def test_iso_roundtrip():
    d = datetime(2026, 9, 10, 18, 30)
    assert s.from_iso(s.to_iso(d)) == d and s.from_iso("bad") is None
