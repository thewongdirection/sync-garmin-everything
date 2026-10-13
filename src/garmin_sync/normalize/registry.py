"""Normalizer registry and the shared context handed to every normalizer."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from garmin_sync.models import Metric, Record

Lookup = Callable[[str, str], Any]


class KeyedBy(StrEnum):
    DAY = "day"  # archive key is an ISO date (or a window start date)
    ID = "id"  # archive key is an opaque id (activities)


@dataclass
class Diagnostics:
    """Counts of data the normalizers could not interpret, surfaced in reports."""

    issues: Counter[tuple[str, str]] = field(default_factory=Counter)

    def warn(self, endpoint: str, message: str) -> None:
        self.issues[(endpoint, message)] += 1

    def to_list(self) -> list[dict[str, object]]:
        return [
            {"endpoint": endpoint, "issue": message, "count": count}
            for (endpoint, message), count in sorted(self.issues.items())
        ]


@dataclass
class Context:
    """Per-run lookup state: the user's UTC offset by day, related payloads, diagnostics."""

    day_offsets: dict[str, int] = field(default_factory=dict)
    fallback_offset: int = 0
    diag: Diagnostics = field(default_factory=Diagnostics)
    #: ``lookup(endpoint, key)`` returns another archived payload (or None). Lets a
    #: normalizer join per-activity details to the activity summary without owning I/O.
    lookup: Lookup = field(default=lambda _endpoint, _key: None)

    @property
    def has_offsets(self) -> bool:
        """False when no day's UTC offset is known, so ``offset_for`` can only guess UTC."""
        return bool(self.day_offsets)

    def offset_for(self, day: str) -> int:
        return self.day_offsets.get(day, self.fallback_offset)

    def related(self, endpoint: str, key: str) -> Any:
        return self.lookup(endpoint, key)


NormalizerFn = Callable[[str, Any, Context], list[Record]]


@dataclass(frozen=True)
class NormalizerSpec:
    endpoint: str
    produces: tuple[Metric, ...]
    fn: NormalizerFn
    keyed_by: KeyedBy = KeyedBy.DAY
    #: Archive rows (same key) the normalizer reads through ``ctx.related``. A record is not
    #: final until every one of them has been fetched: without them it would be incomplete,
    #: and what is written to Google cannot be corrected.
    joins: tuple[str, ...] = ()


REGISTRY: dict[str, NormalizerSpec] = {}


def normalizer(
    endpoint: str,
    *produces: Metric,
    keyed_by: KeyedBy = KeyedBy.DAY,
    joins: tuple[str, ...] = (),
) -> Callable[[NormalizerFn], NormalizerFn]:
    """Register ``fn`` as the normalizer for an archived endpoint."""

    def register(fn: NormalizerFn) -> NormalizerFn:
        if endpoint in REGISTRY:
            raise ValueError(f"duplicate normalizer for {endpoint!r}")
        REGISTRY[endpoint] = NormalizerSpec(endpoint, produces, fn, keyed_by, joins)
        return fn

    return register
