"""Sink interface: a destination that accepts normalized records."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from garmin_sync.models import Metric, Record


class Status(StrEnum):
    PUSHED = "pushed"
    PENDING = "pending"  # accepted, but Google reported it still processing; not recorded as done
    WOULD_PUSH = "would_push"  # dry run
    DUPLICATE_EXISTING = "duplicate_existing"  # destination already holds equivalent data
    UNSUPPORTED = "unsupported"  # record cannot be expressed in this destination
    SKIPPED = "skipped"  # not attempted: write limit reached, or an earlier error stopped the run
    FAILED = "failed"


@dataclass(frozen=True)
class Outcome:
    record: Record
    status: Status
    detail: str | None = None
    remote_ref: str | None = None


OutcomeCallback = Callable[[Outcome], None]


class Sink(Protocol):
    name: str
    #: True if re-pushing a changed record updates the destination copy.
    updates_in_place: bool

    def supports(self, metric: Metric) -> bool: ...

    def push(
        self,
        records: Sequence[Record],
        *,
        apply: bool,
        on_outcome: OutcomeCallback,
        max_writes: int | None = None,
    ) -> None:
        """Write ``records`` (or just report what would be written when ``apply`` is False).

        ``on_outcome`` is called once per record, *as soon as that record's fate is known*,
        so the caller can persist progress incrementally and a crash loses at most the
        record in flight. Every input record gets exactly one outcome. ``max_writes`` caps
        the number of records actually written (or that would be, in a dry run); the rest
        are reported as skipped.
        """
        ...
