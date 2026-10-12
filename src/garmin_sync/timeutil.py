"""Time parsing and formatting helpers for Garmin payloads.

Garmin reports instants three ways: epoch milliseconds, naive "GMT" strings
(``2024-05-01T03:00:00.0`` or ``2024-05-01 03:00:00``) and naive "Local" wall-clock
strings. The UTC offset of a record is the difference between its Local and GMT forms.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

_NAIVE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)

# Garmin reports offsets as exact differences; real zones are whole minutes.
_OFFSET_GRANULARITY_S = 60
# The real world's offsets run from UTC-12:00 to UTC+14:00. Garmin sometimes applies an account's
# offset twice to "Local" values, which lands outside that range, so such a value is not an offset.
MIN_OFFSET_S = -12 * 3600
MAX_OFFSET_S = 14 * 3600


def parse_wall(value: Any) -> datetime | None:
    """Parse a naive Garmin timestamp (GMT or Local string, or epoch ms) as a naive datetime.

    Epoch milliseconds are interpreted as UTC wall time. Returns ``None`` for anything unparseable.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.removesuffix("Z")  # some payloads mark UTC explicitly
        for fmt in _NAIVE_FORMATS:
            try:
                return datetime.strptime(text, fmt)  # noqa: DTZ007 - naive on purpose
            except ValueError:
                continue
    return None


def parse_gmt(value: Any) -> datetime | None:
    """Parse a Garmin GMT string / epoch-ms value into an aware UTC datetime."""
    wall = parse_wall(value)
    return wall.replace(tzinfo=UTC) if wall else None


def offset_seconds(local_value: Any, gmt_value: Any) -> int | None:
    """UTC offset implied by a Local/GMT timestamp pair, in seconds.

    None if either value is missing or the difference is not a possible UTC offset.
    """
    local, gmt = parse_wall(local_value), parse_wall(gmt_value)
    if local is None or gmt is None:
        return None
    raw = (local - gmt).total_seconds()
    offset = int(round(raw / _OFFSET_GRANULARITY_S) * _OFFSET_GRANULARITY_S)
    return offset if MIN_OFFSET_S <= offset <= MAX_OFFSET_S else None


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def iter_days(start: date, end: date) -> Iterator[date]:
    """Every date from ``start`` to ``end`` inclusive."""
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def chunk_days(start: date, end: date, span: int) -> Iterator[tuple[date, date]]:
    """Split ``start..end`` (inclusive) into windows of at most ``span`` days."""
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=span - 1), end)
        yield current, window_end
        current = window_end + timedelta(days=1)


def iso_z(moment: datetime) -> str:
    """RFC 3339 UTC timestamp with a trailing ``Z`` (second precision)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_bounds(day: date, offset_s: int) -> tuple[datetime, datetime]:
    """UTC instants of local midnight at the start and end of ``day`` for a fixed offset."""
    start_local = datetime(day.year, day.month, day.day, tzinfo=UTC)
    start = start_local - timedelta(seconds=offset_s)
    return start, start + timedelta(days=1)


def local_date(moment: datetime, offset_s: int) -> date:
    """Calendar date of ``moment`` in a zone fixed at ``offset_s`` seconds from UTC."""
    return (moment + timedelta(seconds=offset_s)).date()
