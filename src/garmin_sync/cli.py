"""Command line interface. Results go to stdout, progress and errors to stderr.

Exit codes: 0 ok, 1 error, 2 usage, 3 login needed, 4 incomplete.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

from garmin_sync import auth_commands, exitcodes, routine, usertime
from garmin_sync.archive import Archive
from garmin_sync.doctor import next_action, run_doctor
from garmin_sync.endpoints import ESSENTIAL
from garmin_sync.errors import CliError
from garmin_sync.garmin_source import (
    AuthRequired,
    GarminUnreachable,
    RateLimited,
    login_from_tokens,
)
from garmin_sync.inventory import build_inventory, default_map_path, load_known_paths
from garmin_sync.models import Metric, utc_now
from garmin_sync.normalize import build_context, normalize_range
from garmin_sync.paths import AppHome, UnsafeHomeError
from garmin_sync.pull import pull
from garmin_sync.push import push_records
from garmin_sync.reports import coverage_table, shape_of, status_summary
from garmin_sync.sanitize import clean
from garmin_sync.sinks import google_auth
from garmin_sync.sinks.base import Sink
from garmin_sync.sinks.google_client import GoogleClient
from garmin_sync.sinks.google_health import GoogleHealthSink
from garmin_sync.sinks.health_connect import HealthConnectExporter
from garmin_sync.timeutil import parse_day

DEFAULT_DAYS = 7
MAX_RANGE_DAYS = 366
_ERROR_LIMIT = 400
SINK_NAMES = ("google-health", "health-connect")


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class _SanitizedLogHandler(logging.Handler):
    """Show library log records, but only as sanitized single lines.

    python-garminconnect logs Garmin's raw error text (and tracebacks) at WARNING. Left alone,
    Python prints that unfiltered to stderr, which an AI agent in a routine would read.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Never let logging break the caller, and never fall back to the default handler
        # (handleError), which would print the record unsanitized.
        with contextlib.suppress(Exception):
            _log(f"[{record.name}] {clean(record.getMessage(), limit=_ERROR_LIMIT)}")


def _route_library_logging() -> None:
    root = logging.getLogger()
    root.handlers[:] = [_SanitizedLogHandler(level=logging.WARNING)]
    root.setLevel(logging.WARNING)


def _emit(payload: Any, as_json: bool, text: str) -> None:
    print(json.dumps(payload, indent=2, default=str) if as_json else text)


# -- argument parsing -----------------------------------------------------------------------


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{raw!r} is not a whole number") from None
    if value < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return value


def _add_range_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("date range")
    group.add_argument(
        "--days",
        type=_positive_int,
        help=f"last N days including today (default {DEFAULT_DAYS}, at most {MAX_RANGE_DAYS})",
    )
    group.add_argument("--since", help="start date YYYY-MM-DD (instead of --days)")
    group.add_argument("--until", help="end date YYYY-MM-DD (default today)")


def _add_push_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument(
        "--limit", type=_positive_int, help="write at most N records (use 1 for a first test)"
    )
    parser.add_argument("--metrics", help="comma-separated metric names to include")


def _sink_list(raw: str) -> list[str]:
    names = [part.strip() for part in raw.split(",") if part.strip()]
    unknown = [name for name in names if name not in SINK_NAMES]
    if not names or unknown:
        raise argparse.ArgumentTypeError(
            f"choose from {', '.join(SINK_NAMES)} (got {unknown or 'nothing'})"
        )
    return names


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="garmin-sync",
        description="Archive Garmin Connect data and sync what Google can accept.",
    )
    parser.add_argument(
        "--home", help="state directory (default ~/.garmin-sync or $GARMIN_SYNC_HOME)"
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--debug", action="store_true", help="show tracebacks")
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="log in to Garmin and Google (run in your own terminal)")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    garmin = auth_sub.add_parser("garmin", help="Garmin login, or --check the saved session")
    garmin.add_argument("--check", action="store_true", help="verify the saved session only")
    garmin.add_argument("--cn", action="store_true", help="use Garmin China (garmin.cn)")
    google = auth_sub.add_parser("google", help="Google setup and login")
    google_sub = google.add_subparsers(dest="google_command", required=True)
    init = google_sub.add_parser("init", help="validate and save the OAuth client JSON")
    init.add_argument("--client-file", required=True, help="client JSON from Google Cloud")
    login = google_sub.add_parser("login", help="authorize access (hidden prompt for the URL)")
    login.add_argument("--stdin", action="store_true", help="read the redirected URL from stdin")
    google_sub.add_parser("check", help="verify the saved Google login")
    auth_sub.add_parser("export-cloud-env", help="write the cloud routine's variables to a file")

    zone = sub.add_parser("timezone", help="show or set your time zone (needed for 'today')")
    zone.add_argument("name", nargs="?", help="IANA name, for example Asia/Singapore")

    doctor = sub.add_parser("doctor", help="check everything and say what to do next")
    doctor.add_argument("--offline", action="store_true", help="skip the online checks")

    pull_p = sub.add_parser("pull", help="download Garmin data into the local archive")
    _add_range_args(pull_p)
    pull_p.add_argument(
        "--only", help="comma-separated endpoints, or 'activities' (see `coverage` for names)"
    )
    pull_p.add_argument("--with-fit", action="store_true", help="also download original FIT files")
    pull_p.add_argument(
        "--force", action="store_true", help="fetch again even what looks final already"
    )
    pull_p.add_argument("--delay", type=float, default=0.3, help="seconds between Garmin calls")

    push_p = sub.add_parser("push", help="send archived data to a destination")
    push_p.add_argument("--sink", required=True, choices=SINK_NAMES)
    _add_range_args(push_p)
    _add_push_args(push_p)

    sync_p = sub.add_parser("sync", help="pull, then push to the destinations you name")
    _add_range_args(sync_p)
    sync_p.add_argument(
        "--sinks", required=True, type=_sink_list, help="comma-separated destinations"
    )
    sync_p.add_argument("--with-fit", action="store_true")
    sync_p.add_argument("--delay", type=float, default=0.3)
    _add_push_args(sync_p)

    sub.add_parser("routine", help="the unattended run, configured by environment variables")
    sub.add_parser("status", help="show local state (no secrets)")
    sub.add_parser("coverage", help="show what each Garmin endpoint maps to")
    inv = sub.add_parser("inventory", help="list fields your account returns that the map lacks")
    inv.add_argument("--map", type=Path, help="field-map CSV (default: docs/field-map in the repo)")
    shape = sub.add_parser("shape", help="show an archived payload's structure without its values")
    shape.add_argument("endpoint")
    shape.add_argument("--key", help="archive key (default: most recent)")
    return parser


def _today(home: AppHome) -> date:
    """The date it is for the user (not for this machine), or an error that says to ask them."""
    zone = usertime.require_zone(os.environ, usertime.read_saved_zone(home.settings_file))
    if zone.source is usertime.Source.SYSTEM:
        _log(
            f"note: using this machine's time zone ({zone.name}), which is only a guess; confirm "
            f"it with the user, then run `garmin-sync timezone {zone.name}`"
        )
    return usertime.today_in(zone)


def resolve_range(args: argparse.Namespace, today: date) -> tuple[date, date]:
    """Validate and resolve ``--days`` / ``--since`` / ``--until`` to ``(start, end)``.

    ``today`` is the date in the user's own time zone, not the machine's.
    """
    if args.days and args.since:
        raise CliError("use either --days or --since, not both")
    try:
        end = parse_day(args.until) if args.until else today
        start = (
            parse_day(args.since)
            if args.since
            else end - timedelta(days=(args.days or DEFAULT_DAYS) - 1)
        )
    except ValueError:
        raise CliError("dates must look like 2026-03-31") from None
    if start > end:
        raise CliError("start date is after end date")
    if end > today:
        raise CliError("the end date is in the future")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise CliError(f"a range can cover at most {MAX_RANGE_DAYS} days")
    return start, end


# -- commands -------------------------------------------------------------------------------


def cmd_auth(args: argparse.Namespace, home: AppHome) -> int:
    match args.auth_command:
        case "garmin":
            if args.check:
                _log(auth_commands.garmin_check(home))
            else:
                auth_commands.garmin_login(home, is_cn=args.cn, log=_log)
        case "google":
            match args.google_command:
                case "init":
                    auth_commands.google_init(home, args.client_file, log=_log)
                case "login":
                    auth_commands.google_login(home, from_stdin=args.stdin, log=_log)
                case "check":
                    _log(auth_commands.google_check(home))
        case "export-cloud-env":
            auth_commands.export_cloud_env(home, log=_log)
    return exitcodes.OK


def cmd_timezone(args: argparse.Namespace, home: AppHome) -> int:
    """Show the zone in use, or save one (validated) so every later command agrees on 'today'."""
    now = utc_now()
    if args.name:
        home.ensure()
        zone = usertime.save_zone(home.settings_file, args.name)
        if os.environ.get(usertime.ENV_TIMEZONE, "").strip():
            _log(f"warning: {usertime.ENV_TIMEZONE} is set and still takes precedence over this")
        if args.json:
            _emit({"timezone": zone.name, "saved": True}, True, "")
        _log(f"Time zone saved: {zone.describe(now)}")
        return exitcodes.OK
    zone = usertime.require_zone(os.environ, usertime.read_saved_zone(home.settings_file))
    _emit(
        {"timezone": zone.name, "source": zone.source},
        args.json,
        f"{zone.describe(now)}, from {zone.source}; today there is {usertime.today_in(zone, now)}",
    )
    return exitcodes.OK


def cmd_doctor(args: argparse.Namespace, home: AppHome) -> int:
    checks = run_doctor(home, online=not args.offline, http=requests.Session(), env=os.environ)
    action = next_action(checks)
    payload = {"checks": [c.to_dict() for c in checks], "next_action": action}
    lines = [f"[{c.status:4}] {c.name}: {c.message}" for c in checks]
    lines.append(f"Next: {action}" if action else "Everything looks good.")
    _emit(payload, args.json, "\n".join(lines))
    return exitcodes.ERROR if any(c.status == "fail" for c in checks) else exitcodes.OK


def _pull_exit_code(report: dict[str, Any]) -> int:
    """Nonzero if the pull stopped early, or an endpoint the Google writes depend on failed.

    A failing optional endpoint (a feature the device lacks) is reported but is not a failure.
    """
    stopped = {"auth_required": exitcodes.AUTH, "rate_limited": exitcodes.INCOMPLETE}
    if report["aborted"]:
        return stopped.get(report["aborted"], exitcodes.INCOMPLETE)
    return exitcodes.INCOMPLETE if ESSENTIAL & report["failed"].keys() else exitcodes.OK


def _run_pull(args: argparse.Namespace, home: AppHome, today: date) -> tuple[dict[str, Any], str]:
    """Archive the requested range; returns the report dict and a text summary."""
    start, end = resolve_range(args, today)
    only = [name.strip() for name in args.only.split(",")] if getattr(args, "only", None) else None
    home.ensure()
    source = login_from_tokens(home, delay_s=args.delay)
    with Archive.open(home.archive_file) as archive:
        try:
            report = pull(
                source,
                archive,
                start,
                end,
                only=only,
                force=getattr(args, "force", False),
                today=today,
                fit_dir=home.fit_dir if args.with_fit else None,
                progress=_log,
            )
        except ValueError as exc:
            raise CliError(str(exc)) from None
    return report.to_dict(), _format_pull(report.to_dict(), start, end)


def cmd_pull(args: argparse.Namespace, home: AppHome) -> int:
    report, text = _run_pull(args, home, _today(home))
    _emit(report, args.json, text)
    return _pull_exit_code(report)


def _build_sink(name: str, home: AppHome) -> Sink:
    if name == "health-connect":
        return HealthConnectExporter(home.exports_dir)
    client = google_auth.load_client(home.google_client_file)
    http = requests.Session()
    auth = google_auth.GoogleAuth(client, google_auth.FileTokenStore(home.google_token_file), http)
    return GoogleHealthSink(GoogleClient(auth, http))


def _parse_metrics(raw: str | None) -> set[Metric] | None:
    if not raw:
        return None
    try:
        return {Metric(name.strip()) for name in raw.split(",")}
    except ValueError:
        valid = ", ".join(m.value for m in Metric)
        raise CliError(f"unknown metric name; choose from: {valid}") from None


def _push_to(
    sink_names: Sequence[str], args: argparse.Namespace, home: AppHome, today: date
) -> tuple[dict[str, Any], int]:
    """Push to each sink; returns the results and the worst exit code."""
    start, end = resolve_range(args, today)
    metrics = _parse_metrics(args.metrics)
    home.ensure()
    results: dict[str, Any] = {"range": f"{start}..{end}"}
    code = exitcodes.OK
    with Archive.open(home.archive_file) as archive:
        ctx = build_context(archive, start, end)
        records = normalize_range(archive, start, end, ctx)
        results["records_in_range"] = len(records)
        for name in sink_names:
            try:
                report = push_records(
                    _build_sink(name, home),
                    archive,
                    records,
                    apply=args.apply,
                    now=utc_now(),
                    metrics=metrics,
                    limit=args.limit,
                )
            except google_auth.GoogleAuthError as exc:
                results[name] = {"unavailable": clean(exc)}
                code = max(code, exitcodes.AUTH)
                continue
            results[name] = report.to_dict()
            if not report.ok:
                code = max(code, exitcodes.ERROR)
        results["normalization_issues"] = ctx.diag.to_list()
    return results, code


def cmd_push(args: argparse.Namespace, home: AppHome) -> int:
    results, code = _push_to([args.sink], args, home, _today(home))
    _emit(results, args.json, _format_push(results))
    return code


def cmd_sync(args: argparse.Namespace, home: AppHome) -> int:
    today = _today(home)  # once, so a run across midnight pulls and pushes the same days
    pull_report, pull_text = _run_pull(args, home, today)
    code = _pull_exit_code(pull_report)
    if pull_report["aborted"]:
        _emit({"pull": pull_report}, args.json, pull_text)
        return code
    push_results, push_code = _push_to(args.sinks, args, home, today)
    _emit(
        {"pull": pull_report, "push": push_results},
        args.json,
        f"{pull_text}\n{_format_push(push_results)}",
    )
    return max(code, push_code)


def cmd_routine(args: argparse.Namespace, home: AppHome) -> int:
    # No progress callback: stdout must carry the status line first, and stderr only warnings.
    result = routine.run_routine(os.environ, http=requests.Session())
    print(f"STATUS: {result.status.value} - {result.detail}")
    print(json.dumps(result.summary, indent=2, default=str))
    return result.exit_code


def cmd_status(args: argparse.Namespace, home: AppHome) -> int:
    archive = Archive.open(home.archive_file) if home.archive_file.exists() else None
    try:
        summary = status_summary(home, archive, os.environ)
    finally:
        if archive:
            archive.close()
    _emit(summary, args.json, json.dumps(summary, indent=2))
    return exitcodes.OK


def cmd_coverage(args: argparse.Namespace, home: AppHome) -> int:
    rows = coverage_table()
    _emit(rows, args.json, _format_coverage(rows))
    return exitcodes.OK


def cmd_inventory(args: argparse.Namespace, home: AppHome) -> int:
    map_path = args.map or default_map_path()
    if map_path is None or not map_path.exists():
        raise CliError("field map not found; run from the repository or pass --map <csv>")
    if not home.archive_file.exists():
        raise CliError("nothing archived yet; run `pull` first")
    with Archive.open(home.archive_file) as archive:
        report = build_inventory(archive, load_known_paths(map_path))
    unmapped = sum(entry["unmapped_count"] for entry in report.values())
    text = "\n".join(
        f"{name:32} {e['paths_seen']:5} fields seen, {e['unmapped_count']:4} not in the map"
        for name, e in sorted(report.items())
    )
    _emit(
        report, args.json, f"{text}\n{unmapped} unmapped fields in total (use --json for the list)"
    )
    return exitcodes.OK


def cmd_shape(args: argparse.Namespace, home: AppHome) -> int:
    if not home.archive_file.exists():
        raise CliError("nothing archived yet; run `pull` first")
    with Archive.open(home.archive_file) as archive:
        key = args.key or archive.latest_key(args.endpoint)
        if key is None or not archive.has_raw(args.endpoint, key):
            raise CliError(f"nothing archived for {args.endpoint}; run `pull` first")
        payload = {
            "endpoint": args.endpoint,
            "key": key,
            "shape": shape_of(archive.get_raw(args.endpoint, key)),
        }
    print(json.dumps(payload, indent=2))
    return exitcodes.OK


# -- text formatting ------------------------------------------------------------------------


def _format_pull(report: dict[str, Any], start: date, end: date) -> str:
    lines = [
        f"Pulled {start}..{end}: {sum(report['fetched'].values())} fetched, "
        f"{sum(report['skipped'].values())} already final, "
        f"{sum(report['failed'].values())} failed"
    ]
    for name, count in sorted(report["failed"].items()):
        lines.append(f"  {name}: {count} failed (first: {report['first_error'].get(name)})")
    if report["disabled_endpoints"]:
        lines.append(
            "  switched off after repeated failures: " + ", ".join(report["disabled_endpoints"])
        )
    if report["rejected_activity_ids"]:
        lines.append(f"  ignored {report['rejected_activity_ids']} activities with unusable ids")
    if report["aborted"]:
        lines.append(
            f"  STOPPED EARLY: {report['aborted']} - progress is saved, re-run to continue"
        )
    return "\n".join(lines)


def _format_push(results: dict[str, Any]) -> str:
    lines = [f"Range {results['range']}: {results['records_in_range']} normalized records"]
    for name in SINK_NAMES:
        entry = results.get(name)
        if entry is None:
            continue
        if "unavailable" in entry:
            lines.append(f"{name}: not run - {entry['unavailable']}")
            continue
        mode = "APPLIED" if entry["applied"] else "dry run (add --apply to write)"
        lines.append(
            f"{name} [{mode}]: "
            + ", ".join(f"{k}={v}" for k, v in sorted(entry["outcomes"].items()))
        )
        lines.extend(f"  error: {err}" for err in entry["sample_errors"])
    for issue in results["normalization_issues"]:
        lines.append(f"note: {issue['endpoint']}: {issue['issue']} (x{issue['count']})")
    return "\n".join(lines)


def _format_coverage(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'endpoint':38} {'metrics':34} google-health / health-connect"]
    for row in rows:
        if row["archive_only"]:
            lines.append(f"{row['endpoint']:38} {'(archive only)':34}")
            continue
        lines.append(
            f"{row['endpoint']:38} {','.join(row['metrics']):34} "
            f"{','.join(row['google_health']) or '-'} / {','.join(row['health_connect'])}"
        )
    return "\n".join(lines)


# -- entry point ----------------------------------------------------------------------------

_COMMANDS = {
    "auth": cmd_auth,
    "timezone": cmd_timezone,
    "doctor": cmd_doctor,
    "pull": cmd_pull,
    "push": cmd_push,
    "sync": cmd_sync,
    "routine": cmd_routine,
    "status": cmd_status,
    "coverage": cmd_coverage,
    "inventory": cmd_inventory,
    "shape": cmd_shape,
}

_EXPECTED_ERRORS: tuple[tuple[type[BaseException], int], ...] = (
    (CliError, exitcodes.ERROR),
    (UnsafeHomeError, exitcodes.ERROR),
    (usertime.TimezoneError, exitcodes.ERROR),
    (AuthRequired, exitcodes.AUTH),
    (google_auth.GoogleAuthError, exitcodes.AUTH),
    (RateLimited, exitcodes.INCOMPLETE),
    (GarminUnreachable, exitcodes.INCOMPLETE),
)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _route_library_logging()
    home = AppHome.resolve(args.home)
    try:
        return _COMMANDS[args.command](args, home)
    except KeyboardInterrupt:
        _log("interrupted")
        return 130
    except Exception as exc:
        if args.debug:
            raise
        for error_type, code in _EXPECTED_ERRORS:
            if isinstance(exc, error_type):
                _log(f"error: {clean(exc, limit=_ERROR_LIMIT)}")
                return code
        _log(f"error: unexpected {type(exc).__name__} (re-run with --debug for details)")
        return exitcodes.ERROR


if __name__ == "__main__":
    raise SystemExit(main())
