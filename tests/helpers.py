"""Synthetic payload builders and fakes shared by the tests.

Everything here is invented: shapes follow python-garminconnect responses as the code
expects them, values are made up. No real health data or credentials belong in tests.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from garminconnect import GarminConnectConnectionError

EDT = -4 * 3600  # fixed offset used by the fixtures (US Eastern, daylight time)


def ms(stamp: str) -> int:
    """Epoch milliseconds for an ISO UTC timestamp like ``2024-05-02T03:00:00``."""
    return int(datetime.fromisoformat(stamp).replace(tzinfo=UTC).timestamp() * 1000)


def local_ms(gmt_stamp: str, offset_s: int = EDT) -> int:
    """Garmin 'Local' epoch ms: the GMT instant shifted by the offset."""
    return ms(gmt_stamp) + offset_s * 1000


def sleep_payload() -> dict[str, Any]:
    return {
        "dailySleepDTO": {
            "calendarDate": "2024-05-02",
            "sleepStartTimestampGMT": ms("2024-05-02T03:00:00"),
            "sleepEndTimestampGMT": ms("2024-05-02T11:00:00"),
            "sleepStartTimestampLocal": local_ms("2024-05-02T03:00:00"),
            "sleepEndTimestampLocal": local_ms("2024-05-02T11:00:00"),
            "deviceRemCapable": True,
            "sleepTimeSeconds": 28800,
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 16200,
            "remSleepSeconds": 5400,
            "awakeSleepSeconds": 1800,
        },
        "sleepLevels": [
            {
                "startGMT": "2024-05-02T03:00:00.0",
                "endGMT": "2024-05-02T04:30:00.0",
                "activityLevel": 1.0,
            },
            {
                "startGMT": "2024-05-02T04:30:00.0",
                "endGMT": "2024-05-02T06:00:00.0",
                "activityLevel": 0.0,
            },
            {
                "startGMT": "2024-05-02T06:00:00.0",
                "endGMT": "2024-05-02T07:30:00.0",
                "activityLevel": 2.0,
            },
            {
                "startGMT": "2024-05-02T07:30:00.0",
                "endGMT": "2024-05-02T08:00:00.0",
                "activityLevel": 3.0,
            },
            {
                "startGMT": "2024-05-02T08:00:00.0",
                "endGMT": "2024-05-02T11:00:00.0",
                "activityLevel": 1.0,
            },
        ],
    }


def heart_rate_payload() -> dict[str, Any]:
    return {
        "calendarDate": "2024-05-01",
        "startTimestampGMT": ms("2024-05-01T04:00:00"),
        "startTimestampLocal": local_ms("2024-05-01T04:00:00"),
        "restingHeartRate": 52,
        "heartRateValues": [
            [ms("2024-05-01T10:00:00"), 60],
            [ms("2024-05-01T10:02:00"), 62],
            [ms("2024-05-01T10:04:00"), None],  # sensor gap
            [ms("2024-05-01T10:06:00"), 400],  # implausible
            [ms("2024-05-01T11:01:00"), 70],
        ],
    }


def user_summary_payload(day: str = "2024-05-01") -> dict[str, Any]:
    return {
        "calendarDate": day,
        "wellnessStartTimeGmt": f"{day}T04:00:00.0",
        "wellnessEndTimeGmt": _next_day(day) + "T04:00:00.0",
        "wellnessStartTimeLocal": f"{day}T00:00:00.0",
        "totalKilocalories": 2400.0,
        "activeKilocalories": 600.0,
        "totalDistanceMeters": 8200,
        "floorsAscended": 12.0,
    }


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def weigh_in_payload() -> dict[str, Any]:
    return {
        "dailyWeightSummaries": [
            {
                "summaryDate": "2024-05-01",
                "allWeightMetrics": [
                    {
                        "calendarDate": "2024-05-01",
                        "timestampGMT": ms("2024-05-01T11:30:00"),
                        "date": local_ms("2024-05-01T11:30:00"),
                        "weight": 72500.0,
                        "bodyFat": 18.5,
                        "bodyWater": 55.0,
                        "boneMass": 3100,
                    },
                    {
                        "calendarDate": "2024-05-01",
                        "timestampGMT": ms("2024-05-01T12:00:00"),
                        "weight": 5.0,
                    },
                ],
            }
        ]
    }


def activity_summary(activity_id: int = 1001, type_key: str = "running") -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityName": "Morning Run",
        "activityType": {"typeKey": type_key},
        "startTimeGMT": "2024-05-01 10:00:00",
        "startTimeLocal": "2024-05-01 06:00:00",
        "duration": 1800.5,
        "elapsedDuration": 1900.0,
        "distance": 5000.0,
        "calories": 320.0,
        "bmrCalories": 40.0,
        "averageHR": 150.0,
        "maxHR": 175.0,
        "elevationGain": 40.0,
        "averageSpeed": 2.78,
        "steps": 5200,
        "hasPolyline": True,
        "hrTimeInZone_1": 100.0,
        "hrTimeInZone_2": 200.0,
        "hrTimeInZone_3": 600.0,
        "hrTimeInZone_4": 800.0,
        "hrTimeInZone_5": 100.0,
        "moderateIntensityMinutes": 10,
        "vigorousIntensityMinutes": 5,
        "avgStrideLength": 105.0,  # cm
        "avgVerticalOscillation": 8.2,  # cm
        "avgGroundContactTime": 240.0,  # ms
        "avgVerticalRatio": 7.7,
        "averageRunningCadenceInStepsPerMinute": 168.0,
        "vO2MaxValue": 52.0,
    }


def activity_detail(manual: bool = False, manufacturer: str = "GARMIN") -> dict[str, Any]:
    """The ``get_activity`` payload fields the normalizer reads."""
    return {
        "description": "Felt good",
        "metadataDTO": {"manualActivity": manual, "manufacturer": manufacturer},
    }


def splits_payload() -> dict[str, Any]:
    """Three laps and a pause/resume pair inside the 06:00-local run (10:00 GMT, 1900 s elapsed)."""

    def lap(start: str, elapsed: float, active: float) -> dict[str, Any]:
        return {
            "startTimeGMT": start,
            "elapsedDuration": elapsed,
            "duration": active,
            "distance": 1600.0,
            "calories": 100.0,
            "averageHR": 148.0,
            "averageSpeed": 2.7,
            "elevationGain": 12.0,
            "groundContactTime": 238.0,
            "strideLength": 104.0,
            "verticalOscillation": 8.1,
            "verticalRatio": 7.6,
            "averageRunCadence": 166.0,
        }

    return {
        "lapDTOs": [
            lap("2024-05-01T10:00:00.0", 600, 600),
            lap("2024-05-01T10:10:00.0", 700, 600),
            lap("2024-05-01T10:21:40.0", 600, 600),
        ],
        "eventDTOs": [
            {"startTimeGMT": "2024-05-01T10:00:00.0", "sectionTypeDTO": {"key": "start"}},
            {"startTimeGMT": "2024-05-01T10:10:00.0", "sectionTypeDTO": {"key": "pause"}},
            {"startTimeGMT": "2024-05-01T10:11:40.0", "sectionTypeDTO": {"key": "resume"}},
            {"startTimeGMT": "2024-05-01T10:31:40.0", "sectionTypeDTO": {"key": "stop"}},
        ],
    }


class FakeGarmin:
    """Stands in for ``garminconnect.Garmin``: records calls, serves canned payloads."""

    def __init__(self, responses: dict[str, Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.failures: dict[str, Exception] = {}

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def method(*args: Any) -> Any:
            self.calls.append((name, args))
            if name in self.failures:
                raise self.failures[name]
            response = self.responses.get(name, {})
            return response(*args) if callable(response) else response

        return method

    def count(self, name: str) -> int:
        return sum(1 for called, _ in self.calls if called == name)


def unsupported() -> GarminConnectConnectionError:
    return GarminConnectConnectionError("404 not found for this device")
