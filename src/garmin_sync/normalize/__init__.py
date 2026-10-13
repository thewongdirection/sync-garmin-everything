"""Turn archived Garmin payloads into normalized :class:`~garmin_sync.models.Record` objects.

Normalization is a pure function of the archive, so mapping fixes can be re-applied
without re-downloading anything.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

from garmin_sync import endpoints as ep
from garmin_sync.archive import Archive
from garmin_sync.models import Record

# Imported for their side effect: each module registers its normalizers.
from garmin_sync.normalize import activity, body, sleep, vitals  # noqa: F401
from garmin_sync.normalize.registry import REGISTRY, Context, Diagnostics, KeyedBy, NormalizerSpec
from garmin_sync.timeutil import offset_seconds

__all__ = ["REGISTRY", "Context", "Diagnostics", "build_context", "normalize_range"]

_OFFSET_LOOKBACK = timedelta(days=30)
# An id-keyed row (an activity) is filed under the day it started, but belongs to the day it ends,
# which can be later; look back this far for rows that may reach into the requested range.
_ID_LOOKBACK = timedelta(days=7)
# Errors a malformed (or hostile) payload can provoke. One bad row must not stop the others.
_ROW_ERRORS = (ValueError, TypeError, KeyError, OverflowError, ArithmeticError, AttributeError)


def build_context(archive: Archive, start: date, end: date) -> Context:
    """Learn the user's UTC offset for each archived day from ``user_summary`` payloads."""
    offsets: dict[str, int] = {}
    for key, payload in archive.iter_raw(
        "user_summary",
        (start - _OFFSET_LOOKBACK).isoformat(),
        (end + timedelta(days=1)).isoformat(),  # the next day's offset closes a day's interval
    ):
        if not isinstance(payload, dict):
            continue
        offset = offset_seconds(
            payload.get("wellnessStartTimeLocal"), payload.get("wellnessStartTimeGmt")
        )
        if offset is not None:
            offsets[key] = offset
    fallback = offsets[max(offsets)] if offsets else 0
    ctx = Context(day_offsets=offsets, fallback_offset=fallback, lookup=archive.get_raw)
    if not offsets:
        ctx.diag.warn("user_summary", "no timezone offsets archived; assuming UTC")
    return ctx


def _keys_in_scope(
    archive: Archive, spec: NormalizerSpec, start: date, end: date
) -> Iterator[tuple[str, Any]]:
    """Archived rows that can hold records anchored in ``start..end``."""
    if spec.keyed_by is KeyedBy.ID:
        return archive.iter_raw_by_span_end(
            spec.endpoint, (start - _ID_LOOKBACK).isoformat(), end.isoformat()
        )
    # Day rows are keyed by their own day (a day later at most); window rows by the window start.
    span = ep.get_endpoint(spec.endpoint).span_days
    lookback = timedelta(days=max(span, 2) - 1)
    return archive.iter_raw(spec.endpoint, (start - lookback).isoformat(), end.isoformat())


def _provenance(archive: Archive, spec: NormalizerSpec, key: str, ctx: Context) -> datetime | None:
    """When the data behind a record was fetched; the oldest of its rows, None if one is missing."""
    metas = [archive.raw_meta(name, key) for name in (spec.endpoint, *spec.joins)]
    if any(meta is None for meta in metas):
        missing = [n for n, m in zip((spec.endpoint, *spec.joins), metas, strict=True) if m is None]
        ctx.diag.warn(spec.endpoint, f"held back: not fetched yet: {', '.join(missing)}")
        return None
    return min(meta.fetched_at for meta in metas if meta is not None)


def normalize_range(archive: Archive, start: date, end: date, ctx: Context) -> list[Record]:
    """All records anchored on a local day within ``start..end`` (inclusive).

    A record belongs to the local day of its last instant (see ``Record.anchor_date``), so
    a night's sleep is selected by the day you woke up. Overlapping archive windows can
    yield the same measurement twice; ``external_id`` de-duplicates them, keeping the copy
    fetched most recently. Each record is stamped with when its source data was downloaded
    (``None`` while part of that data is still missing, which keeps it from being pushed).
    A payload that cannot be interpreted is reported and skipped.
    """
    by_id: dict[str, Record] = {}
    for spec in REGISTRY.values():
        for key, payload in _keys_in_scope(archive, spec, start, end):
            try:
                found = spec.fn(key, payload, ctx)
            except _ROW_ERRORS as exc:
                ctx.diag.warn(spec.endpoint, f"unreadable payload ({type(exc).__name__})")
                continue
            fetched_at = _provenance(archive, spec, key, ctx) if found else None
            for record in found:
                try:
                    if not start <= record.anchor_date <= end:
                        continue
                    stamped = replace(record, fetched_at=fetched_at)
                except _ROW_ERRORS as exc:  # extreme timestamps overflow date arithmetic
                    ctx.diag.warn(spec.endpoint, f"unreadable payload ({type(exc).__name__})")
                    continue
                known = by_id.get(stamped.external_id)
                if known is None or _newer(stamped, known):
                    by_id[stamped.external_id] = stamped
    return sorted(by_id.values(), key=lambda r: (r.start, r.metric.value, r.external_id))


def _newer(candidate: Record, known: Record) -> bool:
    """Prefer the copy with provenance, then the one fetched more recently."""
    if candidate.fetched_at is None or known.fetched_at is None:
        return known.fetched_at is None and candidate.fetched_at is not None
    return candidate.fetched_at > known.fetched_at
