"""Defensive accessors for Garmin's loosely-typed JSON."""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

from garmin_sync.models import Metric, Record


def num(value: Any) -> float | None:
    """``value`` as a finite float, or None (rejects bools, strings, NaN, inf)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def in_range(value: float | None, low: float, high: float) -> float | None:
    return value if value is not None and low <= value <= high else None


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def pairs(value: Any) -> Iterator[tuple[float, float]]:
    """Yield ``(timestamp, reading)`` from a ``[[ts, value], ...]`` array, skipping bad rows."""
    for item in as_list(value):
        if isinstance(item, list | tuple) and len(item) >= 2:
            ts, reading = num(item[0]), num(item[1])
            if ts is not None and reading is not None:
                yield ts, reading


def from_epoch_ms(epoch_ms: float) -> datetime | None:
    """Aware UTC datetime for epoch milliseconds, or None if out of range."""
    try:
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def calendar_day(value: Any, fallback: str) -> str:
    """``value`` as an ISO date string if it is one, else ``fallback``.

    Garmin's ``calendarDate`` ends up in record ids and, for sleep, in text sent to Google, so
    anything that is not a plain date is not trusted.
    """
    if isinstance(value, str) and len(value) == len("2024-05-01"):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return fallback


def whole_seconds(moment: datetime) -> int:
    return int(moment.timestamp())


def instant(metric: Metric, moment: datetime, offset: int, values: dict[str, Any]) -> Record:
    """An instantaneous record whose id is ``garmin:<metric>:<epoch seconds>``."""
    return Record(
        metric, f"garmin:{metric.value}:{whole_seconds(moment)}", moment, moment, offset, values
    )
