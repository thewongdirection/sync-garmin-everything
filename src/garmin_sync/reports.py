"""Read-only views: what maps where, local state summary, and payload shapes."""

from __future__ import annotations

from typing import Any

from garmin_sync import endpoints as ep
from garmin_sync.archive import Archive
from garmin_sync.coverage import TARGETS
from garmin_sync.garmin_source import TOKEN_FILENAME
from garmin_sync.normalize import REGISTRY
from garmin_sync.paths import AppHome, is_private
from garmin_sync.sanitize import clean
from garmin_sync.sinks.google_auth import FileTokenStore, GoogleAuthError

_MAX_SHAPE_DEPTH = 6
# Keys come from Garmin, so they are cleaned and bounded like any other remote text.
_MAX_SHAPE_KEYS = 200
_MAX_KEY_CHARS = 80


def coverage_table() -> list[dict[str, Any]]:
    """One row per archived endpoint: what it normalizes to and where that can be sent."""
    names = [e.name for e in ep.ENDPOINTS] + [ep.ACTIVITY_SUMMARY]
    names += [name for name, _ in ep.ACTIVITY_DETAILS] + [ep.ACTIVITY_EXERCISE_SETS[0]]
    rows = []
    for name in names:
        spec = REGISTRY.get(name)
        produces = spec.produces if spec else ()
        rows.append(
            {
                "endpoint": name,
                "metrics": [m.value for m in produces],
                "google_health": sorted(
                    {
                        t.google_type
                        for m in produces
                        if (t := TARGETS[m]).google_writable and t.google_type
                    }
                ),
                "health_connect": sorted({TARGETS[m].health_connect for m in produces}),
                "archive_only": spec is None,
            }
        )
    return rows


def status_summary(home: AppHome, archive: Archive | None) -> dict[str, Any]:
    """Local state overview. Never includes token or credential values."""
    garmin_token = home.garmin_token_dir / TOKEN_FILENAME
    google: dict[str, Any] = {
        "client_file": home.google_client_file.exists(),
        "token": home.google_token_file.exists(),
    }
    if home.google_token_file.exists():
        try:
            token = FileTokenStore(home.google_token_file).load()
            google["missing_scopes"] = token.missing_scopes() if token else None
        except GoogleAuthError:
            google["token_unreadable"] = True
        google["private_permissions"] = is_private(home.google_token_file)
    return {
        "home": str(home.root),
        "garmin_login": garmin_token.exists(),
        "garmin_token_private": is_private(garmin_token) if garmin_token.exists() else None,
        "google": google,
        "archive_rows": archive.raw_counts() if archive else {},
        "pushed_to": archive.ledger_counts() if archive else {},
    }


def shape_of(value: Any, depth: int = 0) -> Any:
    """Structure of ``value`` with all scalar values replaced by their type name.

    Safe to share when diagnosing a payload mismatch: it reveals keys, types and list
    lengths but none of the health data.
    """
    if depth >= _MAX_SHAPE_DEPTH:
        return "..."
    if isinstance(value, dict):
        shape = {
            clean(key, limit=_MAX_KEY_CHARS): shape_of(item, depth + 1)
            for key, item in list(value.items())[:_MAX_SHAPE_KEYS]
        }
        if len(value) > _MAX_SHAPE_KEYS:
            shape["\u2026"] = f"{len(value) - _MAX_SHAPE_KEYS} more keys"
        return shape
    if isinstance(value, list):
        return {"list": len(value), "item": shape_of(value[0], depth + 1) if value else None}
    return type(value).__name__
