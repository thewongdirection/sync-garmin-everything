"""Normalized health records, independent of both Garmin's payloads and Google's schemas.

Every normalizer produces :class:`Record` objects; every sink consumes them. The
``values`` keys for each :class:`Metric` are the contract between the two and are
documented on the enum members.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any

from garmin_sync.timeutil import day_bounds, local_date


class Metric(StrEnum):
    """A kind of health measurement. Comments list the ``Record.values`` keys."""

    # Sessions / intervals
    SLEEP = "sleep"  # stages: [{start, end, stage}], total_s, deep_s, light_s, rem_s, awake_s
    EXERCISE = "exercise"  # see normalize.activity for the full key list
    STEPS = "steps"  # count
    DISTANCE = "distance"  # meters
    ACTIVE_CALORIES = "active_calories"  # kcal
    TOTAL_CALORIES = "total_calories"  # kcal
    BASAL_METABOLIC_RATE = "basal_metabolic_rate"  # kcal_per_day
    FLOORS = "floors"  # floors
    HYDRATION = "hydration"  # milliliters
    HEART_RATE = "heart_rate"  # samples: [[epoch_s, bpm], ...]
    SKIN_TEMPERATURE = "skin_temperature"  # delta_c (overnight deviation from baseline)
    # Instantaneous samples
    RESTING_HEART_RATE = "resting_heart_rate"  # bpm
    HRV = "hrv"  # rmssd_ms
    SPO2 = "spo2"  # percent
    RESPIRATORY_RATE = "respiratory_rate"  # breaths_per_min
    WEIGHT = "weight"  # grams (+ optional source_type)
    BODY_FAT = "body_fat"  # percent (+ optional source_type)
    BONE_MASS = "bone_mass"  # grams
    BODY_WATER_MASS = "body_water_mass"  # grams
    BLOOD_PRESSURE = "blood_pressure"  # systolic, diastolic (mmHg)
    VO2_MAX = "vo2_max"  # ml_kg_min


# Metrics whose value keeps growing through the local day (totals, running series). A record
# of one of these is final only once its whole day is over; everything else is a discrete
# event (a night's sleep, a workout, a weigh-in) that is final shortly after it ends.
_ACCUMULATES_DAILY = frozenset(
    {
        Metric.HYDRATION,
        Metric.STEPS,
        Metric.DISTANCE,
        Metric.ACTIVE_CALORIES,
        Metric.TOTAL_CALORIES,
        Metric.BASAL_METABOLIC_RATE,
        Metric.FLOORS,
        Metric.HEART_RATE,
        Metric.RESTING_HEART_RATE,
        Metric.VO2_MAX,
    }
)
# Garmin can still be assembling an event (a just-finished workout, a weigh-in) for a little while
# after it ends, so discrete records must be fetched at least this long after. Sleep takes longer
# to settle: stages are processed after waking and the watch may sync late.
EVENT_GRACE = timedelta(hours=2)
SLEEP_GRACE = timedelta(hours=6)


@dataclass(frozen=True)
class Record:
    """One normalized measurement with a stable identity.

    ``start``/``end`` are aware UTC datetimes (equal for instantaneous samples).
    ``utc_offset_s`` is the user's local offset at the time, needed by both sinks.
    ``external_id`` is stable across runs so re-syncing never duplicates data.
    ``fetched_at`` is when the archived payload it came from was downloaded; it is
    provenance, not content, so it is ignored by equality and by :meth:`content_hash`.
    """

    metric: Metric
    external_id: str
    start: datetime
    end: datetime
    utc_offset_s: int
    values: dict[str, Any] = field(default_factory=dict)
    fetched_at: datetime | None = field(default=None, compare=False)
    #: The UTC offset at ``end`` when it differs from the one at ``start`` (a local day that
    #: contains a clock change is 23 or 25 hours long). Defaults to ``utc_offset_s``.
    end_utc_offset_s: int | None = None

    def __post_init__(self) -> None:
        for name, moment in (("start", self.start), ("end", self.end)):
            if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
                raise ValueError(f"Record.{name} must be an aware UTC datetime")
        if self.end < self.start:
            raise ValueError("Record.end must not be before Record.start")

    @property
    def end_offset(self) -> int:
        return self.utc_offset_s if self.end_utc_offset_s is None else self.end_utc_offset_s

    @property
    def is_instant(self) -> bool:
        return self.start == self.end

    @property
    def last_instant(self) -> datetime:
        """The final moment the record covers (an interval's end is exclusive)."""
        return max(self.start, self.end - timedelta(seconds=1))

    @property
    def anchor_date(self) -> date:
        """The local calendar day the record belongs to.

        This is the day of its last instant: a night's sleep belongs to the day you woke
        up, a daily total to its own day. Range selection and "is it final yet?" both use
        this one definition so they can never disagree.
        """
        return local_date(self.last_instant, self.end_offset)

    def is_settled(self, now: datetime) -> bool:
        """True once the record's local calendar day is over."""
        return self.anchor_date < local_date(now, self.end_offset)

    def is_final(self, now: datetime) -> bool:
        """True if nothing Garmin could still add would change this record.

        The archived payload must have been downloaded *after* the record stopped
        changing: after its local day ended for running totals, or a grace period
        (:data:`EVENT_GRACE`, :data:`SLEEP_GRACE`) after it ended for discrete events.
        Unknown provenance is never final, so records built from an archive that was pulled
        mid-day are held back, not frozen.
        """
        if self.fetched_at is None:
            return False
        if self.metric in _ACCUMULATES_DAILY:
            day_end = day_bounds(self.anchor_date, self.end_offset)[1]
            return self.is_settled(now) and self.fetched_at >= day_end
        settled_at = self.end + (SLEEP_GRACE if self.metric is Metric.SLEEP else EVENT_GRACE)
        return now >= settled_at and self.fetched_at >= settled_at

    def content_hash(self) -> str:
        """Digest of everything that would change what a sink writes."""
        canonical = json.dumps(
            {
                "metric": self.metric.value,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "offset": self.utc_offset_s,
                "end_offset": self.end_offset,
                "values": self.values,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)
