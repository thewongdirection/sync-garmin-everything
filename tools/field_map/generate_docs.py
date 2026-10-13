"""Regenerate docs/field-map/FIELD-MAP.md from the two CSVs next to it.

The CSVs are the source of truth. Edit a row's ``tier`` / ``google_target`` / ``hc_target`` /
``transform`` / ``reason`` there (for example after ``garmin-sync inventory`` finds new fields),
then run::

    python tools/field_map/generate_docs.py

Standard library only. Run it from anywhere; paths default to the repository's docs/field-map.
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, OrderedDict, defaultdict
from collections.abc import Iterable
from pathlib import Path

Row = dict[str, str]

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "docs" / "field-map"
GOOGLE_ROOT_ORDER = (
    "exercise", "sleep", "weight", "bodyFat", "hydrationLog", "height", "nutritionLog",
    "menstrualPeriod", "symptoms", "moods", "ovulationTest", "Profile", "Settings",
)  # fmt: skip
DESTINATIONS = {
    "G+H": "Google Health API write **and** Health Connect export",
    "G": "Google Health API write",
    "H": "Health Connect export only (Google cannot be written)",
    "GO": "Opt-in only (sensitive or changes your Google settings)",
    "A": "Local archive only",
    "M": "Housekeeping (ids, containers, social)",
}
GOOGLE_STATUSES = (
    ("FILLED", "Filled from a Garmin field"),
    ("FILLED (derived/constant)", "Filled by derivation or a constant (e.g. UTC offsets)"),
    ("FILLED (opt-in)", "Fillable, opt-in only"),
    ("writable, no Garmin source", "Writable, but Garmin has nothing to put there"),
    ("output-only", "Set by Google (cannot be written)"),
    ("API type is read-only", "Belongs to a data type the API does not let third parties write"),
)
ENUM_INLINE_MAX = 8
ARCHIVE_REASONS_SHOWN = 25
_WRAPPER_PREFIX = re.compile(
    r"^(\[\]\.|summaryDTO\.|dailyWeightSummaries\[\]\.allWeightMetrics\[\]\.|dateWeightList\[\]\.)"
)


def read_rows(path: Path) -> list[Row]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def md(text: str | None) -> str:
    """Escape a value for a Markdown table cell."""
    return (text or "").replace("|", "\\|").replace("\n", " ")


def overview(garmin: list[Row], google: list[Row]) -> list[str]:
    tiers = Counter(r["tier"] for r in garmin)
    fields = [r for r in garmin if r["path"]]
    by_confidence = Counter(r["confidence"] for r in fields)
    lines = [
        "## 1. Numbers\n",
        "| | Count |\n|---|---|",
        "| Garmin read methods + FIT message types inventoried "
        f"| {len({r['method'] for r in garmin})} |",
        f"| Garmin fields documented (with cited evidence) | {len(fields)} |",
        "| ...of which observed in recorded real responses / read by third-party parsers "
        "/ inferred "
        f"| {by_confidence['observed']} / {by_confidence['code']} / {by_confidence['inferred']} |",
        "| Methods with no field evidence anywhere (discoverable only from your account) "
        f"| {tiers['N']} |",
        f"| Google Health API payload fields (v4) | {len(google)} |",
        "",
        "**Garmin fields by destination**\n",
        "| Destination | Garmin fields |\n|---|---|",
        *(f"| {label} | {tiers.get(tier, 0)} |" for tier, label in DESTINATIONS.items()),
        "",
        "**Google fields by status**\n",
        "| Status | Google fields |\n|---|---|",
    ]
    statuses = Counter(r["status"] for r in google if r["status"] != "container")
    lines += [f"| {label} | {statuses.get(status, 0)} |" for status, label in GOOGLE_STATUSES]
    return [*lines, ""]


def google_type_cell(row: Row) -> str:
    text = row["type"] + (f":{row['format']}" if row["format"] else "")
    count = int(row["enum_count"])
    if count:
        text += (
            f" ({count} values)"
            if count > ENUM_INLINE_MAX
            else f" ({row['enum_values'].replace('|', ', ')})"
        )
    return md(text)


def writable_types(google: list[Row]) -> list[str]:
    lines = [
        "## 2. Google Health API: what we write, field by field\n",
        "Only these data types accept `create` (verified in the Discovery document and the "
        "data-types page). Each table lists every field that is not output-only; civil-time echo "
        "fields are omitted because Google derives them.\n",
    ]
    for root in GOOGLE_ROOT_ORDER:
        rows = sorted(
            (
                r
                for r in google
                if r["google_root"].replace("dataPoint.", "") == root
                and r["status"] not in ("output-only", "container")
                and ".civil" not in r["path"]
            ),
            key=lambda r: r["path"],
        )
        if not rows:
            continue
        filled = sum(r["status"].startswith("FILLED") for r in rows)
        lines += [
            f"### `{root}`  ({filled} of {len(rows)} writable fields filled)\n",
            "| Google field | Type | Status | Garmin source / note |\n|---|---|---|---|",
        ]
        for r in rows:
            note = md(r["garmin_sources"]).replace(";", "<br>")
            lines.append(f"| `{r['path']}` | {google_type_cell(r)} | {r['status']} | {note} |")
        lines.append("")
    return lines


def health_connect_only(garmin: list[Row]) -> list[str]:
    candidates = [
        r
        for r in garmin
        if r["tier"] == "H" and r["hc_target"] and not r["hc_target"].startswith("(")
    ]
    merged: OrderedDict[tuple[str, str, str], list[str]] = OrderedDict()
    for r in sorted(candidates, key=lambda r: (r["hc_target"], r["method"], r["path"])):
        path = _WRAPPER_PREFIX.sub("", r["path"])
        merged.setdefault((r["hc_target"], path, r["transform"]), []).append(r["method"])
    lines = [
        "## 3. Health Connect export: data Google's API will not accept\n",
        "These Garmin fields have no writable Google field, but Health Connect (Android, "
        "on-device) has a record for them. They go into the export file for an Android "
        "companion app.\n",
        "| Health Connect record | Garmin field | From (method) | Transform / note |\n"
        "|---|---|---|---|",
    ]
    for (target, path, how), methods in merged.items():
        sources = ", ".join(f"`{m}`" for m in dict.fromkeys(methods))
        lines.append(f"| `{md(target)}` | `{md(path)}` | {sources} | {md(how)} |")
    lines += [
        "",
        "_Every Health Connect record also needs a timestamp and zone offset; they come from the "
        "paired GMT/Local fields of the same response (for example `startTimestampGMT`/"
        "`startTimestampLocal`, `wellnessStartTimeGmt`/`wellnessStartTimeLocal`)._\n",
    ]
    return lines


def archive_only(garmin: list[Row]) -> list[str]:
    reasons = Counter(r["reason"] for r in garmin if r["tier"] == "A")
    lines = [
        "## 4. Archive only: everything else, and why\n",
        "These fields are still downloaded and kept in the local SQLite archive (so nothing is "
        "lost and mappings can be revised without re-downloading), but have no Google or Health "
        "Connect destination.\n",
        "| Reason | Fields |\n|---|---|",
        *(f"| {md(k)} | {v} |" for k, v in reasons.most_common(ARCHIVE_REASONS_SHOWN)),
        "",
    ]
    by_method: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for r in garmin:
        if r["path"]:
            by_method[r["method"]][r["tier"]] += 1
    lines += [
        "### Per-method coverage\n",
        "| Method / FIT message | Fields | Google (G, G+H) | Health Connect only | Opt-in | "
        "Archive only | Housekeeping |\n|---|---|---|---|---|---|---|",
    ]
    for method in sorted(by_method):
        c = by_method[method]
        lines.append(
            f"| `{method}` | {sum(c.values())} | {c['G'] + c['G+H']} | {c['H']} | {c['GO']} "
            f"| {c['A']} | {c['M']} |"
        )
    undocumented: Iterable[str] = (f"`{r['method']}`" for r in garmin if r["tier"] == "N")
    return [
        *lines,
        "\n### Methods with no field evidence (zero documented fields)\n",
        ", ".join(undocumented),
    ]


def build(garmin: list[Row], google: list[Row]) -> str:
    lines = [
        "# Field map: Garmin Connect -> Google Health API / Health Connect\n",
        "_Generated from the CSVs next to this file by `tools/field_map/generate_docs.py` (see "
        "`docs/field-map/README.md`). The CSVs contain every field._\n",
        *overview(garmin, google),
        *writable_types(google),
        *health_connect_only(garmin),
        *archive_only(garmin),
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="folder holding the CSVs")
    args = parser.parse_args()
    text = build(
        read_rows(args.dir / "garmin-fields.csv"), read_rows(args.dir / "google-fields.csv")
    )
    (args.dir / "FIELD-MAP.md").write_text(text, encoding="utf-8")
    print(f"wrote {args.dir / 'FIELD-MAP.md'} ({len(text.splitlines())} lines)")


if __name__ == "__main__":
    main()
