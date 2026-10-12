"""Daily activity: steps, daily totals and exercise sessions (with laps, pauses, zones)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from garmin_sync.models import Metric, Record
from garmin_sync.normalize.activity_types import map_activity_type
from garmin_sync.normalize.registry import Context, KeyedBy, normalizer
from garmin_sync.normalize.util import as_dict, as_list, calendar_day, in_range, num
from garmin_sync.sanitize import clean
from garmin_sync.timeutil import offset_seconds, parse_gmt

_MAX_ACTIVITY = timedelta(days=7)
_MAX_LAPS = 500
_NAME_LIMIT = 200
_NOTES_LIMIT = 2000
_PACE_TYPES = frozenset({"RUNNING", "RUNNING_TREADMILL", "WALKING", "HIKING"})

# Garmin lap events -> Google ExerciseEventType. The accepted spellings are not documented,
# so anything else makes the whole event list be dropped rather than half-written.
_TOTAL_FIELDS = (
    ("distance_m", "distance", 2e7),
    ("calories_kcal", "calories", 1e5),
    ("avg_hr", "averageHR", 300),
    ("max_hr", "maxHR", 300),
    ("elevation_gain_m", "elevationGain", 1e5),
    ("steps", "steps", 1e7),
    ("avg_speed_mps", "averageSpeed", 100),
    ("swim_lengths", "activeLengths", 1e5),
)
_LAP_FIELDS = (
    ("active_s", "duration", 7 * 86400),
    ("distance_m", "distance", 2e7),
    ("calories_kcal", "calories", 1e5),
    ("avg_hr", "averageHR", 300),
    ("avg_speed_mps", "averageSpeed", 100),
    ("elevation_gain_m", "elevationGain", 1e5),
)
_EVENT_TYPES = {
    "start": "START",
    "stop": "STOP",
    "pause": "PAUSE",
    "resume": "RESUME",
    "auto_pause": "AUTO_PAUSE",
    "auto_resume": "AUTO_RESUME",
}


@normalizer("steps_data", Metric.STEPS)
def normalize_steps(key: str, payload: Any, ctx: Context) -> list[Record]:
    offset = ctx.offset_for(key)
    records = []
    for item in as_list(payload):
        interval = as_dict(item)
        start = parse_gmt(interval.get("startGMT"))
        end = parse_gmt(interval.get("endGMT"))
        steps = num(interval.get("steps"))
        if start is None or end is None or not start < end <= start + timedelta(days=1):
            continue
        if not steps or steps <= 0:
            continue
        records.append(
            Record(
                Metric.STEPS,
                f"garmin:steps:{int(start.timestamp())}",
                start,
                end,
                offset,
                {"count": int(steps)},
            )
        )
    return records


_DAILY_TOTALS = (
    (Metric.TOTAL_CALORIES, "totalKilocalories", "kcal"),
    (Metric.ACTIVE_CALORIES, "activeKilocalories", "kcal"),
    (Metric.BASAL_METABOLIC_RATE, "bmrKilocalories", "kcal_per_day"),
    (Metric.DISTANCE, "totalDistanceMeters", "meters"),
    (Metric.FLOORS, "floorsAscended", "floors"),
)


@normalizer("user_summary", *(metric for metric, _, _ in _DAILY_TOTALS))
def normalize_user_summary(key: str, payload: Any, ctx: Context) -> list[Record]:
    data = as_dict(payload)
    start = parse_gmt(data.get("wellnessStartTimeGmt"))
    end = parse_gmt(data.get("wellnessEndTimeGmt"))
    if start is None or end is None or not start < end <= start + timedelta(days=2):
        return []
    offset = offset_seconds(data.get("wellnessStartTimeLocal"), data.get("wellnessStartTimeGmt"))
    if offset is None:
        offset = ctx.offset_for(key)
    day = calendar_day(data.get("calendarDate"), key)

    records = []
    for metric, source_key, unit_key in _DAILY_TOTALS:
        value = num(data.get(source_key))
        if value is None or value <= 0:
            continue
        records.append(
            Record(metric, f"garmin:{metric.value}:{day}", start, end, offset, {unit_key: value})
        )
    return records


# -- exercise -------------------------------------------------------------------------------


@normalizer(
    "activity_summary",
    Metric.EXERCISE,
    keyed_by=KeyedBy.ID,
    joins=("activity", "activity_splits"),
)
def normalize_activity(key: str, payload: Any, ctx: Context) -> list[Record]:
    summary = as_dict(payload)
    start = parse_gmt(summary.get("startTimeGMT"))
    active_s = num(summary.get("duration"))
    if start is None or active_s is None or active_s <= 0:
        ctx.diag.warn("activity_summary", "activity without start time or duration")
        return []
    elapsed_s = num(summary.get("elapsedDuration"))
    # The clock time can never be shorter than the timer.
    elapsed_s = max(elapsed_s or 0.0, active_s)
    if elapsed_s > _MAX_ACTIVITY.total_seconds():
        ctx.diag.warn("activity_summary", "implausibly long activity")
        return []
    offset = offset_seconds(summary.get("startTimeLocal"), summary.get("startTimeGMT"))
    if offset is None:
        offset = ctx.offset_for(start.date().isoformat())
    end = start + timedelta(seconds=round(elapsed_s))

    detail = as_dict(ctx.related("activity", key))
    metadata = as_dict(detail.get("metadataDTO"))
    type_key = as_dict(summary.get("activityType")).get("typeKey") or as_dict(
        detail.get("activityTypeDTO")
    ).get("typeKey")
    google_type, hc_type = map_activity_type(type_key)

    values: dict[str, Any] = {
        "activity_id": key,
        "garmin_type": type_key if isinstance(type_key, str) else None,
        "google_type": google_type,
        "hc_type": hc_type,
        "name": clean(summary.get("activityName") or "Garmin activity", limit=_NAME_LIMIT),
        "active_s": round(active_s),
        "elapsed_s": round(elapsed_s),
        "has_gps": bool(summary.get("hasPolyline")),
        "manual": bool(summary.get("manualActivity") or metadata.get("manualActivity")),
    }
    if isinstance(manufacturer := metadata.get("manufacturer"), str) and manufacturer:
        values["manufacturer"] = clean(manufacturer, limit=60)
    description = summary.get("description") or detail.get("description")
    if isinstance(description, str) and description.strip():
        values["description"] = clean(description, limit=_NOTES_LIMIT)

    _add_totals(values, summary, hc_type)
    _add_zones(values, summary, ctx.related("activity_hr_zones", key))
    _add_mobility(values, summary)
    if (laps := _laps(ctx.related("activity_splits", key), start, end)) and len(laps) > 1:
        values["laps"] = laps
    if events := _events(ctx.related("activity_splits", key), start, end, ctx):
        values["events"] = events

    return [Record(Metric.EXERCISE, f"garmin:exercise:{key}", start, end, offset, values)]


def _add_totals(values: dict[str, Any], summary: dict[str, Any], hc_type: str) -> None:
    for field_name, source_key, high in _TOTAL_FIELDS:
        value = in_range(num(summary.get(source_key)), 0, high)
        if value is not None:
            values[field_name] = value
    calories, basal = num(summary.get("calories")), num(summary.get("bmrCalories"))
    if calories is not None and basal is not None and calories >= basal >= 0:
        values["active_calories_kcal"] = round(calories - basal, 1)
    speed = values.get("avg_speed_mps")
    if speed and speed > 0 and hc_type in _PACE_TYPES:
        values["avg_pace_s_per_m"] = round(1 / speed, 4)
    if hc_type.startswith("RUNNING") and (vo2 := num(summary.get("vO2MaxValue"))):
        values["run_vo2_max"] = vo2
    moderate, vigorous = (
        num(summary.get("moderateIntensityMinutes")),
        num(summary.get("vigorousIntensityMinutes")),
    )
    if moderate is not None or vigorous is not None:
        # Assumes both services count a vigorous minute double (field-map decision 3).
        values["active_zone_minutes"] = round((moderate or 0) + 2 * (vigorous or 0))


def _add_zones(values: dict[str, Any], summary: dict[str, Any], zones_payload: Any) -> None:
    """Seconds in Google's four zones from Garmin's five (Z1+Z2 light, Z3, Z4, Z5 peak)."""
    seconds = {n: num(summary.get(f"hrTimeInZone_{n}")) for n in range(1, 6)}
    if all(v is None for v in seconds.values()):
        for item in as_list(zones_payload):
            zone = as_dict(item)
            number, secs = num(zone.get("zoneNumber")), num(zone.get("secsInZone"))
            if number is not None and 1 <= number <= 5 and secs is not None:
                seconds[int(number)] = secs
    if all(v is None for v in seconds.values()):
        return
    secs = {n: max(v or 0.0, 0.0) for n, v in seconds.items()}
    values["hr_zone_s"] = {
        "light": round(secs[1] + secs[2]),
        "moderate": round(secs[3]),
        "vigorous": round(secs[4]),
        "peak": round(secs[5]),
    }


def _mobility(source: dict[str, Any], keys: dict[str, tuple[str, ...]]) -> dict[str, float]:
    """Running-dynamics fields under whichever spelling this payload uses."""
    found = {}
    for name, candidates in keys.items():
        for candidate in candidates:
            value = num(source.get(candidate))
            if value is not None and value > 0:
                found[name] = value
                break
    return found


_SUMMARY_MOBILITY = {
    "ground_contact_ms": ("avgGroundContactTime", "groundContactTime"),
    "stride_cm": ("avgStrideLength", "strideLength"),
    "vertical_oscillation_cm": ("avgVerticalOscillation", "verticalOscillation"),
    "vertical_ratio": ("avgVerticalRatio", "verticalRatio"),
    "cadence_spm": ("averageRunningCadenceInStepsPerMinute", "averageRunCadence"),
}
_LAP_MOBILITY = {
    "ground_contact_ms": ("groundContactTime",),
    "stride_cm": ("strideLength",),
    "vertical_oscillation_cm": ("verticalOscillation",),
    "vertical_ratio": ("verticalRatio",),
    "cadence_spm": ("averageRunCadence",),
}


def _add_mobility(values: dict[str, Any], summary: dict[str, Any]) -> None:
    if found := _mobility(summary, _SUMMARY_MOBILITY):
        values["mobility"] = found


def _laps(splits: Any, activity_start: datetime, activity_end: datetime) -> list[dict[str, Any]]:
    """Laps inside the activity window, as plain dicts of epoch seconds and SI units."""
    laps = []
    for item in as_list(as_dict(splits).get("lapDTOs"))[:_MAX_LAPS]:
        lap = as_dict(item)
        start = parse_gmt(lap.get("startTimeGMT"))
        length = num(lap.get("elapsedDuration")) or num(lap.get("duration"))
        if start is None or not length or length <= 0:
            continue
        end = start + timedelta(
            seconds=int(length)
        )  # truncate: rounding up can overlap the next lap
        if start < activity_start - timedelta(seconds=1) or end > activity_end + timedelta(
            seconds=1
        ):
            continue
        entry: dict[str, Any] = {"start": int(start.timestamp()), "end": int(end.timestamp())}
        for field_name, source_key, high in _LAP_FIELDS:
            value = in_range(num(lap.get(source_key)), 0, high)
            if value is not None:
                entry[field_name] = value
        if found := _mobility(lap, _LAP_MOBILITY):
            entry["mobility"] = found
        laps.append(entry)
    laps.sort(key=lambda lap: lap["start"])
    # Google requires laps not to overlap: clip each to the next one's start, drop empty ones.
    for current, following in zip(laps, laps[1:], strict=False):
        current["end"] = min(current["end"], following["start"])
    return [lap for lap in laps if lap["end"] > lap["start"]]


def _event_type(item: dict[str, Any]) -> str | None:
    section = as_dict(item.get("sectionTypeDTO"))
    raw = section.get("key") or section.get("sectionTypeKey") or item.get("eventType")
    if not isinstance(raw, str):
        return None
    return _EVENT_TYPES.get(raw.strip().lower().replace("-", "_").replace(" ", "_"))


def _events(
    splits: Any, activity_start: datetime, activity_end: datetime, ctx: Context
) -> list[dict[str, Any]]:
    """Pause/resume events, or nothing at all if any event is not understood."""
    raw_events = as_list(as_dict(splits).get("eventDTOs"))
    events = []
    for item in raw_events:
        event = as_dict(item)
        moment, kind = parse_gmt(event.get("startTimeGMT")), _event_type(event)
        if moment is None or kind is None or not activity_start <= moment <= activity_end:
            ctx.diag.warn(
                "activity_splits", "unrecognised event; pause events skipped for activity"
            )
            return []
        events.append({"time": int(moment.timestamp()), "type": kind})
    events.sort(key=lambda e: e["time"])
    return events
