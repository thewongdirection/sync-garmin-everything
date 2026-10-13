"""``inventory``: compare the fields *your* Garmin account returns with the field map.

Garmin publishes no schema, so the map in docs/field-map is built from recorded responses
and third-party parsers. This walks the payloads you have archived and lists every key path
the map does not know, with the value *types* seen but never the values, so the output is
safe to share and to paste into a bug report.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from garmin_sync import endpoints as ep
from garmin_sync.archive import Archive
from garmin_sync.sanitize import clean

MAP_RELATIVE = Path("docs") / "field-map" / "garmin-fields.csv"
_MAX_DEPTH = 12
_MAX_KEYS = 500
_MAX_KEY_CHARS = 80
_MAX_PATHS = 5000


def default_map_path() -> Path | None:
    """The field map next to a source checkout, or None for an installed package."""
    candidate = Path(__file__).resolve().parents[2] / MAP_RELATIVE
    return candidate if candidate.exists() else None


def method_for(endpoint: str) -> str:
    """The python-garminconnect method an archive name came from."""
    if endpoint == ep.ACTIVITY_SUMMARY:
        return "get_activities_by_date"
    for name, method in (*ep.ACTIVITY_DETAILS, ep.ACTIVITY_EXERCISE_SETS):
        if endpoint == name:
            return method
    return f"get_{endpoint}"


def load_known_paths(path: Path) -> dict[str, set[str]]:
    """``{method: {field path, ...}}`` from the field-map CSV."""
    known: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            known[row["method"]].add(row["path"])
    return known


def flatten(value: Any, prefix: str = "", depth: int = 0) -> Iterator[tuple[str, str]]:
    """Yield ``(path, type name)`` for every node; list items share the path ``name[]``."""
    if depth > _MAX_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in list(value.items())[:_MAX_KEYS]:
            name = clean(key, limit=_MAX_KEY_CHARS)  # keys are remote text: clean and bound them
            path = f"{prefix}.{name}" if prefix else name
            yield path, _type_name(item)
            yield from flatten(item, path, depth + 1)
    elif isinstance(value, list):
        for item in value:
            path = f"{prefix}[]"
            yield path, _type_name(item)
            yield from flatten(item, path, depth + 1)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    return {dict: "object", list: "array", str: "str", int: "int", float: "float"}.get(
        type(value), type(value).__name__
    )


def build_inventory(archive: Archive, known: dict[str, set[str]]) -> dict[str, Any]:
    """Observed key paths per archived endpoint, split into mapped and unmapped."""
    report: dict[str, Any] = {}
    for endpoint in archive.raw_counts():
        method_known = known.get(method_for(endpoint), set())
        observed: dict[str, set[str]] = defaultdict(set)
        for _key, payload in archive.iter_raw(endpoint, "", "\U0010ffff"):
            # Activity summaries are stored one per row but listed as `[].field` in the map.
            document = [payload] if endpoint == ep.ACTIVITY_SUMMARY else payload
            for path, type_name in flatten(document):
                if path in observed or len(observed) < _MAX_PATHS:
                    observed[path].add(type_name)
        unmapped = {p: sorted(t) for p, t in sorted(observed.items()) if p not in method_known}
        report[endpoint] = {
            "method": method_for(endpoint),
            "paths_seen": len(observed),
            "unmapped_count": len(unmapped),
            "unmapped": unmapped,
        }
    return report
