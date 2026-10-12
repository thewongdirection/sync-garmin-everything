"""Body: weigh-ins/composition, blood pressure, hydration, VO2 max."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

from garmin_sync.models import Metric, Record
from garmin_sync.normalize.registry import Context, normalizer
from garmin_sync.normalize.util import as_dict, as_list, in_range, instant, num
from garmin_sync.timeutil import day_bounds, offset_seconds, parse_day, parse_gmt


def _weigh_in_entries(payload: Any) -> Iterator[dict[str, Any]]:
    data = as_dict(payload)
    for summary in as_list(data.get("dailyWeightSummaries")):
        for entry in as_list(as_dict(summary).get("allWeightMetrics")):
            yield as_dict(entry)
    for entry in as_list(data.get("dateWeightList")):
        yield as_dict(entry)


@normalizer("weigh_ins", Metric.WEIGHT, Metric.BODY_FAT, Metric.BODY_WATER_MASS, Metric.BONE_MASS)
def normalize_weigh_ins(key: str, payload: Any, ctx: Context) -> list[Record]:
    records = []
    for entry in _weigh_in_entries(payload):
        moment = parse_gmt(entry.get("timestampGMT") or entry.get("date"))
        grams = in_range(num(entry.get("weight")), 20_000, 500_000)
        if moment is None or grams is None:
            continue
        offset = offset_seconds(entry.get("date"), entry.get("timestampGMT"))
        if offset is None:
            offset = ctx.offset_for(str(entry.get("calendarDate") or key))
        # Only a value Garmin reports as manual is passed on; other source types are undocumented.
        manual = {"source_type": "MANUAL"} if entry.get("sourceType") == "MANUAL" else {}

        records.append(instant(Metric.WEIGHT, moment, offset, {"grams": grams, **manual}))
        if (fat := in_range(num(entry.get("bodyFat")), 1, 80)) is not None:
            records.append(instant(Metric.BODY_FAT, moment, offset, {"percent": fat, **manual}))
        if (water_pct := in_range(num(entry.get("bodyWater")), 1, 90)) is not None:
            water_grams = round(grams * water_pct / 100, 1)
            records.append(instant(Metric.BODY_WATER_MASS, moment, offset, {"grams": water_grams}))
        if (bone := in_range(num(entry.get("boneMass")), 100, 20_000)) is not None:
            records.append(instant(Metric.BONE_MASS, moment, offset, {"grams": bone}))
    return records


def _blood_pressure_entries(payload: Any) -> Iterator[dict[str, Any]]:
    for summary in as_list(as_dict(payload).get("measurementSummaries")):
        for entry in as_list(as_dict(summary).get("measurements")):
            yield as_dict(entry)


@normalizer("blood_pressure", Metric.BLOOD_PRESSURE)
def normalize_blood_pressure(key: str, payload: Any, ctx: Context) -> list[Record]:
    records = []
    for entry in _blood_pressure_entries(payload):
        moment = parse_gmt(entry.get("measurementTimestampGMT"))
        systolic = in_range(num(entry.get("systolic")), 40, 300)
        diastolic = in_range(num(entry.get("diastolic")), 20, 200)
        if moment is None or systolic is None or diastolic is None:
            continue
        offset = offset_seconds(
            entry.get("measurementTimestampLocal"), entry.get("measurementTimestampGMT")
        )
        if offset is None:
            offset = ctx.offset_for(moment.date().isoformat())
        records.append(
            instant(
                Metric.BLOOD_PRESSURE,
                moment,
                offset,
                {"systolic": systolic, "diastolic": diastolic},
            )
        )
    return records


@normalizer("hydration_data", Metric.HYDRATION)
def normalize_hydration(key: str, payload: Any, ctx: Context) -> list[Record]:
    data = as_dict(payload)
    millilitres = num(data.get("valueInML"))
    if millilitres is None or millilitres <= 0:
        return []
    try:
        day = parse_day(str(data.get("calendarDate") or key))
    except ValueError:
        ctx.diag.warn("hydration_data", "unreadable calendarDate")
        return []
    if not ctx.has_offsets:
        # The record is a whole local day, and what is written to Google cannot be corrected, so
        # the day's edges must not be guessed in UTC.
        ctx.diag.warn("hydration_data", "no timezone known; hydration skipped")
        return []
    # A local day runs from one local midnight to the next, each in the offset of its own day: on
    # the day clocks change, or after travel, it is not 24 hours long.
    offset = ctx.offset_for(day.isoformat())
    start = day_bounds(day, offset)[0]
    next_day = day + timedelta(days=1)
    end_offset = ctx.offset_for(next_day.isoformat())
    end = day_bounds(next_day, end_offset)[0]
    if end <= start:
        ctx.diag.warn("hydration_data", "implausible day boundaries")
        return []
    return [
        Record(
            Metric.HYDRATION,
            f"garmin:hydration:{day.isoformat()}",
            start,
            end,
            offset,
            {"milliliters": millilitres},
            end_utc_offset_s=end_offset,
        )
    ]


@normalizer("max_metrics", Metric.VO2_MAX)
def normalize_max_metrics(key: str, payload: Any, ctx: Context) -> list[Record]:
    items = payload if isinstance(payload, list) else [payload]
    for item in items:
        generic = as_dict(as_dict(item).get("generic"))
        vo2 = in_range(
            num(generic.get("vo2MaxPreciseValue") or generic.get("vo2MaxValue")), 10, 100
        )
        if vo2 is None:
            continue
        try:
            day = parse_day(str(generic.get("calendarDate") or key))
        except ValueError:
            ctx.diag.warn("max_metrics", "unreadable calendarDate")
            continue
        offset = ctx.offset_for(day.isoformat())
        moment = day_bounds(day, offset)[0]
        return [
            Record(
                Metric.VO2_MAX,
                f"garmin:vo2_max:{day.isoformat()}",
                moment,
                moment,
                offset,
                {"ml_kg_min": vo2},
            )
        ]
    return []
