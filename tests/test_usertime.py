"""The user's time zone: validated, never guessed, and the source of "today"."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from garmin_sync import usertime

SGT = "Asia/Singapore"
# The conftest replaces this with a stub so tests never see the machine's zone; keep the real one.
real_system_zone_name = usertime.system_zone_name


def zone(name: str = SGT, source: usertime.Source = usertime.Source.ENV) -> usertime.UserZone:
    return usertime.UserZone.from_name(name, source)


# -- names ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", [SGT, "Asia/Kolkata", "America/New_York", " Asia/Singapore ", "UTC"]
)
def test_iana_names_are_accepted(name: str) -> None:
    assert usertime.load_zone(name).key == name.strip()


@pytest.mark.parametrize(
    "name",
    [
        "SGT",  # an abbreviation: ambiguous, so never accepted
        "EST",
        "GMT+8",
        "UTC+8",
        "+08:00",
        "Singapore time",
        "",
        "   ",
        "/etc/passwd",
        "../../etc/passwd",
        "Asia/../../etc/passwd",
        ".hidden",
        "Asia//Singapore",
        "Etc/GMT-8",  # a fixed offset whose sign is the opposite of what it looks like
        "Singapore",  # a legacy alias, not a Region/City name
        "CET",
        "Asia/Singa\npore",  # control characters
        "Asia/Singapore\x1b[31m",
        "A" * 500,
        "Asia/Nowhere",
    ],
)
def test_anything_else_is_refused_with_a_message_that_says_what_to_use(name: str) -> None:
    with pytest.raises(usertime.TimezoneError) as caught:
        usertime.load_zone(name)
    assert usertime.EXAMPLE in str(caught.value)
    assert "\x1b" not in str(caught.value) and "\n" not in str(caught.value)


@pytest.mark.parametrize("bad", [None, 8, ["Asia/Singapore"]])
def test_non_text_is_refused(bad: object) -> None:
    with pytest.raises(usertime.TimezoneError):
        usertime.load_zone(bad)  # type: ignore[arg-type]


# -- which zone applies -----------------------------------------------------------------------


def test_the_environment_wins_over_the_saved_setting() -> None:
    chosen = usertime.resolve_zone(
        {usertime.ENV_TIMEZONE: SGT}, "America/New_York", use_system=True
    )
    assert (chosen.name, chosen.source) == (SGT, "env")


def test_the_saved_setting_applies_when_the_environment_is_silent() -> None:
    chosen = usertime.resolve_zone({}, SGT, use_system=True)
    assert (chosen.name, chosen.source) == (SGT, "settings")
    assert (
        usertime.resolve_zone({usertime.ENV_TIMEZONE: "  "}, SGT, use_system=True).source
        == "settings"
    )


def test_nothing_set_means_unknown_so_the_caller_must_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    assert usertime.resolve_zone({}, None, use_system=True) is None
    with pytest.raises(usertime.TimezoneError, match="not set"):
        usertime.require_zone({}, None)
    monkeypatch.setattr(usertime, "system_zone_name", lambda: "Asia/Tokyo")
    assert usertime.resolve_zone({}, None, use_system=False) is None
    assert usertime.resolve_zone({}, None, use_system=True).source == "system"


def test_a_bad_value_is_an_error_not_a_quiet_fallback() -> None:
    with pytest.raises(usertime.TimezoneError):
        usertime.resolve_zone({usertime.ENV_TIMEZONE: "SGT"}, SGT, use_system=True)
    with pytest.raises(usertime.TimezoneError):
        usertime.resolve_zone({}, "SGT", use_system=True)


def test_a_utc_machine_is_not_taken_as_the_users_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    for tz in ("UTC", "Etc/UTC", "GMT", ":UTC", ""):
        monkeypatch.setenv("TZ", tz)
        monkeypatch.setattr("os.path.realpath", lambda _p: "/usr/share/zoneinfo/Etc/UTC")
        assert real_system_zone_name() is None


def test_a_configured_machine_zone_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", SGT)
    assert real_system_zone_name() == SGT
    monkeypatch.delenv("TZ")
    monkeypatch.setattr("os.path.realpath", lambda _p: "/usr/share/zoneinfo/Asia/Tokyo")
    assert real_system_zone_name() == "Asia/Tokyo"


# -- today ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("utc_moment", "singapore_today"),
    [
        (datetime(2024, 5, 3, 15, 59, tzinfo=UTC), date(2024, 5, 3)),
        (datetime(2024, 5, 3, 16, 0, tzinfo=UTC), date(2024, 5, 4)),  # SGT midnight
        (datetime(2024, 5, 3, 23, 7, tzinfo=UTC), date(2024, 5, 4)),  # the 07:07 SGT run
        (datetime(2024, 5, 4, 11, 7, tzinfo=UTC), date(2024, 5, 4)),  # the 19:07 SGT run
    ],
)
def test_today_is_the_date_in_the_users_zone_not_in_utc(
    utc_moment: datetime, singapore_today: date
) -> None:
    assert usertime.today_in(zone(), utc_moment) == singapore_today


def test_today_across_zones_at_one_instant() -> None:
    moment = datetime(2024, 5, 3, 23, 7, tzinfo=UTC)
    assert usertime.today_in(zone("America/New_York"), moment) == date(2024, 5, 3)
    assert usertime.today_in(zone("Pacific/Kiritimati"), moment) == date(2024, 5, 4)


def test_today_uses_the_clock_when_no_moment_is_given(freeze_time) -> None:
    freeze_time(datetime(2024, 5, 3, 23, 7, tzinfo=UTC))
    assert usertime.today_in(zone()) == date(2024, 5, 4)


def test_descriptions_show_the_offset_including_half_hours_and_daylight_saving() -> None:
    summer, winter = datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)
    assert zone().describe(summer) == "Asia/Singapore (UTC+08:00)"
    assert zone("Asia/Kolkata").describe(summer) == "Asia/Kolkata (UTC+05:30)"
    assert zone("America/New_York").describe(summer) == "America/New_York (UTC-04:00)"
    assert zone("America/New_York").describe(winter) == "America/New_York (UTC-05:00)"


# -- agreement with what Garmin reports -------------------------------------------------------


def test_the_offset_at_local_midnight_follows_daylight_saving() -> None:
    new_york = zone("America/New_York")
    assert usertime.local_midnight_offset(zone(), date(2024, 5, 1)) == 8 * 3600
    assert usertime.local_midnight_offset(new_york, date(2024, 3, 9)) == -5 * 3600
    assert usertime.local_midnight_offset(new_york, date(2024, 3, 11)) == -4 * 3600


def test_days_that_disagree_with_the_zone_are_listed_in_order() -> None:
    offsets = {"2024-05-03": -4 * 3600, "2024-05-01": 8 * 3600, "2024-05-02": -4 * 3600}
    assert usertime.mismatched_days(zone(), offsets) == ["2024-05-02", "2024-05-03"]
    assert usertime.mismatched_days(zone(), {"2024-05-01": 8 * 3600}) == []


def test_unreadable_day_keys_are_ignored() -> None:
    assert usertime.mismatched_days(zone(), {"not-a-day": 0, "": 1}) == []


# -- the saved setting ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"timezone": "Asia/Singapore"}', SGT),
        ('{"timezone": 8}', None),
        ('{"other": 1}', None),
        ("[1, 2]", None),
        ("not json", None),
        ("", None),
    ],
)
def test_a_saved_zone_is_read_defensively(
    tmp_path: Path, content: str, expected: str | None
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(content)
    assert usertime.read_saved_zone(settings) == expected


def test_a_missing_settings_file_means_no_saved_zone(tmp_path: Path) -> None:
    assert usertime.read_saved_zone(tmp_path / "absent.json") is None


# -- review findings --------------------------------------------------------------------------

SENTINEL = "1//0gSECRETREFRESHTOKENVALUE-abcdefghijklmnop"  # pragma: allowlist secret


def test_an_error_never_repeats_the_rejected_value() -> None:
    """Regression: a token pasted into the zone variable was echoed back into the run's output."""
    for source in usertime.Source:
        with pytest.raises(usertime.TimezoneError) as caught:
            usertime.UserZone.from_name(SENTINEL, source)
        assert "SECRET" not in str(caught.value) and SENTINEL[:12] not in str(caught.value)
    with pytest.raises(usertime.TimezoneError) as caught:
        usertime.load_zone(SENTINEL)
    assert "SECRET" not in str(caught.value)


def test_an_error_says_which_setting_is_wrong() -> None:
    with pytest.raises(usertime.TimezoneError, match=usertime.ENV_TIMEZONE):
        usertime.resolve_zone({usertime.ENV_TIMEZONE: "SGT"}, SGT, use_system=True)
    with pytest.raises(usertime.TimezoneError, match="saved time zone"):
        usertime.resolve_zone({}, "SGT", use_system=True)


def test_a_naive_moment_is_refused_rather_than_read_in_the_machines_zone() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        usertime.today_in(zone(), datetime(2024, 5, 3, 23, 7))


def test_clock_changes_inside_a_window_are_not_a_mismatch() -> None:
    new_york = zone("America/New_York")
    spring = {"2024-03-09": -5 * 3600, "2024-03-10": -5 * 3600, "2024-03-11": -4 * 3600}
    assert usertime.mismatched_days(new_york, spring) == []
    assert usertime.mismatched_days(new_york, {"2024-03-10": 8 * 3600}) == ["2024-03-10"]


@pytest.mark.parametrize("reported", [2 * 3600, 3 * 3600])
def test_a_day_whose_midnight_does_not_exist_accepts_either_side_of_the_change(
    reported: int,
) -> None:
    """Cairo's clocks went forward at 00:00 on 25 April 2025, so that midnight never happened."""
    assert usertime.mismatched_days(zone("Africa/Cairo"), {"2025-04-25": reported}) == []


def test_the_machines_tz_variable_wins_over_localtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.path.realpath", lambda _p: "/usr/share/zoneinfo/Asia/Tokyo")
    monkeypatch.setenv("TZ", "UTC")
    assert real_system_zone_name() is None  # TZ says UTC, as it would to every other program
    monkeypatch.delenv("TZ")
    assert real_system_zone_name() == "Asia/Tokyo"


def test_a_zone_can_be_saved_over_a_damaged_settings_file(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    for damaged in ("{bad", "[1]", '"text"', ""):
        settings.write_text(damaged)
        assert usertime.save_zone(settings, SGT).name == SGT
        assert usertime.read_saved_zone(settings) == SGT


def test_saving_keeps_other_settings_and_is_private(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text('{"keep": 1}')
    usertime.save_zone(settings, SGT)
    assert settings.read_text().replace(" ", "").replace("\n", "") in (
        '{"keep":1,"timezone":"Asia/Singapore"}',
    )
    assert settings.stat().st_mode & 0o077 == 0


def test_saving_does_not_follow_a_symlink(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text("untouched")
    settings = tmp_path / "settings.json"
    settings.symlink_to(target)
    usertime.save_zone(settings, SGT)
    assert target.read_text() == "untouched"
    assert usertime.read_saved_zone(settings) == SGT


def test_an_invalid_zone_is_not_saved(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    with pytest.raises(usertime.TimezoneError):
        usertime.save_zone(settings, "SGT")
    assert not settings.exists()
