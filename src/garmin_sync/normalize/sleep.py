"""Sleep: ``get_sleep_data`` -> a SLEEP record per night, plus the overnight series it carries.

The same payload also holds overnight SpO2 and respiration readings and the skin-temperature
deviation; those are emitted as their own records so the dedicated endpoints and the sleep
payload converge on the same ids.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from garmin_sync.models import Metric, Record
from garmin_sync.normalize.registry import Context, normalizer
from garmin_sync.normalize.util import (
    as_dict,
    as_list,
    calendar_day,
    in_range,
    num,
    whole_seconds,
)
from garmin_sync.timeutil import offset_seconds, parse_gmt

# ``sleepLevels[].activityLevel`` codes depend on the device. Watches that measure REM use
# 0 deep / 1 light / 2 REM / 3 awake (-1 = unmeasurable); older ones use 0 deep / 1 light /
# 2 or 3 awake. Mapping 2 to REM on every device would invent REM sleep.
STAGES_WITH_REM = {0: "deep", 1: "light", 2: "rem", 3: "awake"}
STAGES_WITHOUT_REM = {0: "deep", 1: "light", 2: "awake", 3: "awake"}
_MAX_SLEEP = timedelta(hours=24)
# Google wants stages that are contiguous and cover the whole night. Short unmeasured spells are
# reported as awake; if more than this much of the night has no stage data, no stages are sent
# (the night is then written as plain, unstaged sleep) rather than inventing hours of wakefulness.
_MAX_FILLED_GAP = timedelta(minutes=10)
_MAX_FILLED_TOTAL = timedelta(minutes=20)
# Garmin's "Local" sleep times are sometimes offset twice. A night's own offset can legitimately
# differ from the day's by a daylight-saving hour, so only a larger disagreement is distrusted.
_OFFSET_DISAGREEMENT_S = 3 * 3600


def _rem_capable(dto: dict[str, Any], payload: dict[str, Any], ctx: Context) -> bool:
    """Whether the recording device reports REM, which selects the stage-code table."""
    flags = {
        flag
        for flag in (dto.get("deviceRemCapable"), payload.get("remSleepData"))
        if isinstance(flag, bool)
    }
    if len(flags) == 1:
        return flags.pop()
    if not flags and (num(dto.get("remSleepSeconds")) or 0) > 0:
        return True  # the night has REM time, so the device measures it
    reason = "conflicting" if flags else "unknown"
    ctx.diag.warn("sleep_data", f"device REM capability {reason}; treating stage code 2 as awake")
    return False


def _wake_offset(dto: dict[str, Any], start_offset: int) -> int | None:
    """The offset at waking when a clock change happened overnight, else None (same as start)."""
    offset = offset_seconds(dto.get("sleepEndTimestampLocal"), dto.get("sleepEndTimestampGMT"))
    if offset is None or offset == start_offset or abs(offset - start_offset) > 2 * 3600:
        return None
    return offset


def _night_offset(dto: dict[str, Any], key: str, ctx: Context) -> int:
    offset = offset_seconds(dto.get("sleepStartTimestampLocal"), dto.get("sleepStartTimestampGMT"))
    if offset is None:
        return ctx.offset_for(key)
    known = ctx.day_offsets.get(key)
    if known is not None and abs(offset - known) > _OFFSET_DISAGREEMENT_S:
        ctx.diag.warn("sleep_data", "sleep Local time disagrees with the day's UTC offset")
        return known
    return offset


@normalizer(
    "sleep_data",
    Metric.SLEEP,
    Metric.SPO2,
    Metric.RESPIRATORY_RATE,
    Metric.SKIN_TEMPERATURE,
)
def normalize_sleep(key: str, payload: Any, ctx: Context) -> list[Record]:
    data = as_dict(payload)
    dto = as_dict(data.get("dailySleepDTO"))
    start = parse_gmt(dto.get("sleepStartTimestampGMT"))
    end = parse_gmt(dto.get("sleepEndTimestampGMT"))
    if start is None or end is None:
        return []  # no sleep recorded for this day
    if not start < end <= start + _MAX_SLEEP:
        ctx.diag.warn("sleep_data", "implausible sleep window")
        return []

    offset = _night_offset(dto, key, ctx)
    night = calendar_day(dto.get("calendarDate"), key)

    stage_table = STAGES_WITH_REM if _rem_capable(dto, data, ctx) else STAGES_WITHOUT_REM
    stages = _stages(data.get("sleepLevels"), start, end, stage_table)
    if not stages and data.get("sleepLevels"):
        ctx.diag.warn("sleep_data", "stage data too incomplete to send; written without stages")
    values: dict[str, Any] = {"stages": stages}
    for field_name, source_key in (
        ("total_s", "sleepTimeSeconds"),
        ("deep_s", "deepSleepSeconds"),
        ("light_s", "lightSleepSeconds"),
        ("rem_s", "remSleepSeconds"),
        ("awake_s", "awakeSleepSeconds"),
    ):
        value = num(dto.get(source_key))
        if value is not None:
            values[field_name] = int(value)

    records = [
        Record(
            Metric.SLEEP,
            f"garmin:sleep:{night}",
            start,
            end,
            offset,
            values,
            end_utc_offset_s=_wake_offset(dto, offset),
        )
    ]
    records += _overnight_spo2(data, offset)
    records += _overnight_respiration(data, offset)
    if (delta := in_range(num(data.get("avgSkinTempDeviationC")), -10, 10)) is not None:
        records.append(
            Record(
                Metric.SKIN_TEMPERATURE,
                f"garmin:skin_temperature:{night}",
                start,
                end,
                offset,
                {"delta_c": delta},
            )
        )
    return records


def _stages(
    levels: Any, sleep_start: datetime, sleep_end: datetime, table: dict[int, str]
) -> list[dict[str, Any]]:
    """Contiguous stages covering the whole night, or [] if too much of it is unmeasured."""
    segments = []
    for level in as_list(levels):
        item = as_dict(level)
        raw_level = num(item.get("activityLevel"))
        seg_start = parse_gmt(item.get("startGMT"))
        seg_end = parse_gmt(item.get("endGMT"))
        stage = table.get(round(raw_level)) if raw_level is not None else None
        if stage and seg_start and seg_end:
            segments.append((seg_start, seg_end, stage))
    segments.sort(key=lambda s: s[0])

    stages: list[dict[str, Any]] = []
    cursor = sleep_start

    filled = timedelta(0)

    def add(begin: datetime, finish: datetime, stage: str) -> None:
        if int(finish.timestamp()) <= int(begin.timestamp()):
            return  # a sub-second sliver would be a zero-length stage
        if stages and stages[-1]["stage"] == stage and stages[-1]["end"] == int(begin.timestamp()):
            stages[-1]["end"] = int(finish.timestamp())  # merge touching segments of one stage
        else:
            stages.append(
                {"start": int(begin.timestamp()), "end": int(finish.timestamp()), "stage": stage}
            )

    for seg_start, seg_end, stage in segments:
        seg_start, seg_end = max(seg_start, cursor), min(seg_end, sleep_end)
        if seg_end <= seg_start:
            continue
        if seg_start - cursor > _MAX_FILLED_GAP:
            return []
        if seg_start > cursor:
            filled += seg_start - cursor
            add(cursor, seg_start, "awake")
        add(seg_start, seg_end, stage)
        cursor = seg_end
    if not stages or sleep_end - cursor > _MAX_FILLED_GAP:
        return []
    if sleep_end > cursor:
        filled += sleep_end - cursor
        add(cursor, sleep_end, "awake")
    return stages if filled <= _MAX_FILLED_TOTAL else []


def _overnight_spo2(data: dict[str, Any], offset: int) -> list[Record]:
    records = []
    for item in as_list(data.get("wellnessEpochSPO2DataDTOList")):
        reading = as_dict(item)
        moment = parse_gmt(reading.get("epochTimestamp"))
        percent = in_range(num(reading.get("spo2Reading")), 50, 100)
        if moment is None or percent is None:
            continue
        records.append(
            Record(
                Metric.SPO2,
                f"garmin:spo2:{whole_seconds(moment)}",
                moment,
                moment,
                offset,
                {"percent": percent},
            )
        )
    return records


def _overnight_respiration(data: dict[str, Any], offset: int) -> list[Record]:
    records = []
    for item in as_list(data.get("wellnessEpochRespirationDataDTOList")):
        reading = as_dict(item)
        moment = parse_gmt(reading.get("startTimeGMT"))
        rate = in_range(num(reading.get("respirationValue")), 3, 70)
        if moment is None or rate is None:
            continue
        records.append(
            Record(
                Metric.RESPIRATORY_RATE,
                f"garmin:respiratory_rate:{whole_seconds(moment)}",
                moment,
                moment,
                offset,
                {"breaths_per_min": rate},
            )
        )
    return records
