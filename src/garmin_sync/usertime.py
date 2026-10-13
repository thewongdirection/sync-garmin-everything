"""The user's time zone, and "today" as they experience it.

Almost everything here is anchored to the user's local calendar day: a night's sleep belongs to the
day they woke up, a day's hydration runs midnight to midnight, "the last 3 days" means their last
3 days. Each Garmin record carries its own UTC offset, so stored data is already right. But the
*current* day has to come from somewhere, and a cloud machine's clock is UTC: at 07:07 in
Singapore it is still yesterday there, so a window ending "today" would miss this morning.

So the zone is an explicit setting (an IANA name such as ``Asia/Singapore``). The machine's own
zone is only a hint that must be confirmed. If nothing is set the caller must say so and ask, not
carry on in UTC.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from garmin_sync.paths import read_json, write_private_json

ENV_TIMEZONE = "GARMIN_SYNC_TIMEZONE"
EXAMPLE = "Asia/Singapore"
NOT_SET = "your time zone is not set"
ASK = (
    "Ask the user which time zone they are in, then run "
    f"`garmin-sync timezone <their zone, e.g. {EXAMPLE}>`"
)
_UTC_NAMES = frozenset({"UTC", "Etc/UTC", "Etc/GMT", "GMT", "Zulu", "Etc/Zulu", "UCT"})
_ZONEINFO_MARKER = "zoneinfo/"
_MAX_NAME = 64
_NAME_CHARS = re.compile(r"[A-Za-z0-9_+/-]+")


class TimezoneError(Exception):
    """The time zone is missing or not a valid IANA name; the message says what to do.

    Messages never repeat the offending value: it may have been pasted into the wrong setting, and
    a time zone variable sits beside secrets.
    """


class Source(StrEnum):
    ENV = "env"
    SETTINGS = "settings"
    SYSTEM = "system"


_SOURCE_LABELS = {
    Source.ENV: ENV_TIMEZONE,
    Source.SETTINGS: "the saved time zone setting",
    Source.SYSTEM: "this machine's time zone",
}


@dataclass(frozen=True)
class UserZone:
    """A resolved zone and where it came from."""

    name: str
    source: Source
    zone: ZoneInfo

    @classmethod
    def from_name(cls, name: str, source: Source) -> UserZone:
        """``name`` as a zone from ``source``, or a :class:`TimezoneError` naming that source."""
        try:
            zone = load_zone(name)
        except TimezoneError as exc:
            raise TimezoneError(f"{_SOURCE_LABELS[source]}: {exc}") from None
        return cls(zone.key, source, zone)

    def utc_offset_at(self, moment: datetime) -> timedelta:
        offset = moment.astimezone(self.zone).utcoffset()
        return offset if offset is not None else timedelta(0)

    def describe(self, now: datetime) -> str:
        total = int(self.utc_offset_at(now).total_seconds())
        sign = "+" if total >= 0 else "-"
        hours, minutes = divmod(abs(total) // 60, 60)
        return f"{self.name} (UTC{sign}{hours:02d}:{minutes:02d})"


def load_zone(name: str) -> ZoneInfo:
    """``name`` as a zone, or a :class:`TimezoneError` that explains the expected form."""
    text = name.strip() if isinstance(name, str) else ""
    # ZoneInfo reads files, so refuse anything that is not a plain Region/City-style name.
    if not text or len(text) > _MAX_NAME or not _NAME_CHARS.fullmatch(text):
        raise _bad_name()
    if text.startswith(("/", ".")) or ".." in text or "//" in text:
        raise _bad_name()
    # The database also holds legacy fixed-offset names (EST, CET, Etc/GMT+8 which means UTC-8)
    # that look right and are silently wrong half the year or in sign; only Region/City names
    # follow the user's real clock.
    if text not in _UTC_NAMES and ("/" not in text or text.startswith("Etc/")):
        raise _bad_name()
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise _bad_name() from None


def _bad_name() -> TimezoneError:
    return TimezoneError(
        f"the value is not a time zone name; use an IANA Region/City name like {EXAMPLE}, "
        "America/New_York or Europe/London (abbreviations and offsets such as SGT, EST or "
        "UTC+8 are ambiguous)"
    )


def system_zone_name() -> str | None:
    """The machine's configured zone as an IANA name, if it can be told (None if not, or UTC).

    ``TZ`` decides when it is set, as it does for every other program on the machine; otherwise
    ``/etc/localtime`` is read.
    """
    if "TZ" in os.environ:
        candidates = [os.environ["TZ"].strip().lstrip(":")]
    else:
        try:
            target = os.path.realpath("/etc/localtime")
        except OSError:
            target = ""
        candidates = [target.split(_ZONEINFO_MARKER, 1)[1]] if _ZONEINFO_MARKER in target else []
    for name in candidates:
        if name and name not in _UTC_NAMES:
            try:
                load_zone(name)
            except TimezoneError:
                continue
            return name
    return None


def resolve_zone(env: Mapping[str, str], saved: str | None, *, use_system: bool) -> UserZone | None:
    """The zone to use: the environment variable, else the saved setting, else the system's.

    ``use_system`` says whether the machine's own zone may stand in. It is only a hint (the result
    has ``source == "system"``), so callers that must be certain pass False.

    Returns None when none is set and the system's is unknown or UTC: that means "not clear", and
    the caller should ask rather than guess. A value that is set but invalid raises, naming where
    it was set.
    """
    for source, value in ((Source.ENV, env.get(ENV_TIMEZONE, "")), (Source.SETTINGS, saved or "")):
        if value.strip():
            return UserZone.from_name(value, source)
    if use_system and (name := system_zone_name()):
        return UserZone.from_name(name, Source.SYSTEM)
    return None


def require_zone(env: Mapping[str, str], saved: str | None) -> UserZone:
    """The zone to use for "today", or a :class:`TimezoneError` that says to ask the user."""
    zone = resolve_zone(env, saved, use_system=True)
    if zone is None:
        raise TimezoneError(f"{NOT_SET}. {ASK} (or set {ENV_TIMEZONE})")
    return zone


def today_in(zone: UserZone | ZoneInfo, now: datetime | None = None) -> date:
    """The calendar date it currently is in ``zone``. ``now`` must be timezone-aware."""
    tz = zone.zone if isinstance(zone, UserZone) else zone
    if now is not None and now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return (now or datetime.now(UTC)).astimezone(tz).date()


def local_midnight_offset(zone: UserZone, day: date) -> int:
    """The zone's UTC offset, in seconds, at the start of ``day`` (what Garmin reports for it)."""
    offset = datetime(day.year, day.month, day.day, tzinfo=zone.zone).utcoffset()
    return int(offset.total_seconds()) if offset is not None else 0


def _plausible_offsets(zone: UserZone, day: date) -> set[int]:
    """Offsets the zone had during ``day``: at its start, at noon, and at the next midnight.

    On a day whose clock change happens at midnight the start of the day may not exist, so Garmin
    may report the offset in force just after it; all three are accepted.
    """
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=zone.zone).utcoffset()
    following = day + timedelta(days=1)
    return {
        local_midnight_offset(zone, day),
        local_midnight_offset(zone, following),
        int(noon.total_seconds()) if noon is not None else 0,
    }


def mismatched_days(zone: UserZone, day_offsets: Mapping[str, int]) -> list[str]:
    """Days whose Garmin UTC offset disagrees with ``zone`` (travel, or a wrong setting)."""
    wrong = []
    for key, garmin_offset in sorted(day_offsets.items()):
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if garmin_offset not in _plausible_offsets(zone, day):
            wrong.append(key)
    return wrong


def read_saved_zone(settings_file: Path) -> str | None:
    """The zone saved by ``garmin-sync timezone``, or None if there is none or it is unreadable."""
    value = _read_settings(settings_file).get("timezone")
    return value if isinstance(value, str) else None


def save_zone(settings_file: Path, name: str) -> UserZone:
    """Validate ``name`` and keep it in the settings file; other settings are preserved.

    A damaged settings file is replaced rather than being a reason the zone cannot be saved.
    """
    zone = UserZone.from_name(name, Source.SETTINGS)
    write_private_json(settings_file, {**_read_settings(settings_file), "timezone": zone.name})
    return zone


def _read_settings(settings_file: Path) -> dict:
    try:
        content = read_json(settings_file)
    except (OSError, ValueError):
        return {}
    return content if isinstance(content, dict) else {}
