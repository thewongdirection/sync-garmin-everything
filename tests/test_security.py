"""Repo-wide guards: nothing sensitive can be committed and risky constructs stay out."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
}


def repo_files() -> list[Path]:
    """Files git would consider committing: tracked or untracked-but-not-ignored."""
    try:
        listing = subprocess.run(  # noqa: S603 - fixed argv, no user input
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],  # noqa: S607
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split("\n")
        files = [ROOT / name for name in listing if name]
    except (OSError, subprocess.CalledProcessError):
        files = [p for p in ROOT.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.parts)]
    return [p for p in files if p.is_file()]


ALLOW_MARKER = "pragma: allowlist secret"  # explicit, greppable opt-out for fake test values

TEXT_SUFFIXES = {
    *(".py", ".md", ".toml", ".json", ".txt", ".yml", ".yaml", ".cfg", ".ini", ".sh"),
    *(".csv", ".env", ""),
}
MAX_SCANNED_BYTES = 5_000_000

# High-signal credential shapes. Test fixtures use obviously fake values that do not match.
SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "Google OAuth access token": re.compile(r"ya29\.[0-9A-Za-z_\-]{30,}"),
    "Google OAuth client secret": re.compile(r"GOCSPX-[0-9A-Za-z_\-]{20,}"),
    "Google refresh token": re.compile(r"1//0[0-9A-Za-z_\-]{30,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "bearer token literal": re.compile(r"Bearer\s+[A-Za-z0-9\-_\.=]{40,}"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
    "assigned password/secret literal": re.compile(
        r"""(?ix)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*["'][^"'\s]{12,}["']"""
    ),
    # KEY=value lines as found in .env files and the routine's variable list (no spaces around the
    # "=", which is how they are written; Python constants are `NAME = "..."`)
    "environment-style secret": re.compile(
        r"""(?mx)^[ \t]*(?:export[ \t]+)?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_?KEY)[A-Z0-9_]*
            =["']?(?![$<{])[A-Za-z0-9+/=_.:{}-]{16,}"""
    ),
    # "refresh_token": "..." as found in token files (underscore-joined names defeat \btoken\b)
    "JSON secret value": re.compile(
        r"""(?ix)"(?:[a-z_]*token|client_secret|password|private_key)"\s*:\s*"[^"\s]{16,}\""""
    ),
}


def scannable() -> list[Path]:
    return [
        p
        for p in repo_files()
        if p.suffix in TEXT_SUFFIXES and p.stat().st_size < MAX_SCANNED_BYTES
    ]


@pytest.mark.parametrize("label", sorted(SECRET_PATTERNS))
def test_no_credentials_anywhere_in_the_repository(label: str) -> None:
    pattern = SECRET_PATTERNS[label]
    hits = [
        f"{path.relative_to(ROOT)}:{i}"
        for path in scannable()
        for i, line in enumerate(path.read_text(errors="ignore").splitlines(), 1)
        if pattern.search(line) and ALLOW_MARKER not in line
    ]
    assert not hits, f"possible {label}: {hits}"


@pytest.mark.parametrize(
    ("label", "leak"),
    [
        ("environment-style secret", "GARMIN_SYNC_GOOGLE_REFRESH_TOKEN=" + "1a2b3c4d5e6f7a8b9c0d"),
        ("environment-style secret", "export CLIENT_SECRET='" + "x" * 24 + "'"),
        ("JSON secret value", '{"refresh_token": "' + "y" * 30 + '"}'),
        ("JSON secret value", '{"di_refresh_token":"' + "z" * 40 + '"}'),
    ],
)
def test_scanner_catches_the_leak_shapes_the_first_version_missed(label: str, leak: str) -> None:
    assert SECRET_PATTERNS[label].search(leak)


def test_scanner_would_actually_catch_a_leak() -> None:
    assert SECRET_PATTERNS["Google OAuth access token"].search("ya29." + "a" * 40)
    assert SECRET_PATTERNS["assigned password/secret literal"].search(
        "pass" + 'word = "' + "x" * 16 + '"'
    )
    assert SECRET_PATTERNS["private key block"].search("-----BEGIN RSA " + "PRIVATE KEY-----")


@pytest.mark.parametrize(
    "path",
    [
        "garmin_tokens.json",
        "google_token.json",
        "google_oauth_pending.json",
        "google_client.json",
        "client_secret_123.json",
        "archive.sqlite3",
        ".garmin-sync/x",
        ".garminconnect/x",
        "exports/a.jsonl",
        "fit/1.zip",
        "a.fit",
        "a.tcx",
        "a.gpx",
        ".env",
        ".env.local",
        "your_data/x",
        "cassettes/x.yaml",
        "k.pem",
        # temp files left by an interrupted atomic write, and other likely secret carriers
        ".archive.sqlite3.ab12cd.tmp",
        "archive.sqlite",
        "backup.db",
        "token.json",
        "token_backup.json",
        "tokens.json",
        "cloud-env.txt",
        "healthconnect-20240510T150000Z.jsonl",
        "export.jsonl",
        "routine.env",
        "cloud.env",
        "prod-env.txt",
        "archive.sqlite3.bak",
        "secrets.json",
        "secret_backup.json",
    ],
)
def test_gitignore_blocks_sensitive_artifacts(path: str) -> None:
    result = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "--quiet", path],  # noqa: S607
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, f"{path} is not git-ignored"


def test_example_env_files_are_not_ignored() -> None:
    assert (
        subprocess.run(
            ["git", "check-ignore", "--quiet", ".env.example"], cwd=ROOT, check=False
        ).returncode
        == 1
    )  # noqa: S603, S607


DANGEROUS = {
    "shell=True": re.compile(r"shell\s*=\s*True"),
    "eval()": re.compile(r"\beval\("),
    "exec()": re.compile(r"\bexec\("),
    "pickle": re.compile(r"\b(pickle|marshal|shelve)\b"),
    "os.system": re.compile(r"os\.system\("),
    "TLS verification off": re.compile(r"verify\s*=\s*False"),
    "unsafe yaml": re.compile(r"yaml\.load\("),
    "string-built SQL": re.compile(r"""execute\(\s*f["']"""),
    "http:// endpoint": re.compile(r"""["']http://(?!localhost|127\.0\.0\.1)"""),
}


@pytest.mark.parametrize("label", sorted(DANGEROUS))
def test_source_avoids_risky_constructs(label: str) -> None:
    hits = [
        f"{path.relative_to(ROOT)}:{i}"
        for path in (ROOT / "src").rglob("*.py")
        for i, line in enumerate(path.read_text().splitlines(), 1)
        if DANGEROUS[label].search(line)
    ]
    assert not hits, f"{label}: {hits}"


SENSITIVE_NAMES = {
    "access_token",
    "refresh_token",
    "client_secret",
    "password",
    "verifier",
    "garmin_tokens",
    "google_refresh_token",
    "google_client_secret",
    "redirected",
    "redirected_url",
    "token_json",
    "fields",
    "body",
}
OUTPUT_CALLS = {
    "print",
    "_log",
    "log",
    "_emit",
    "progress",
    "write",
    "writelines",
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "critical",
}
SECRET_ENV_KEY = re.compile(r"TOKEN|SECRET|PASSWORD|REFRESH", re.I)


def _called_name(call: ast.Call) -> str:
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _sensitive_names_in(node: ast.AST, extra: frozenset[str] = frozenset()) -> set[str]:
    found = set()
    names = SENSITIVE_NAMES | extra
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in names:
            found.add(child.id)
        elif isinstance(child, ast.Attribute) and child.attr in names:
            found.add(child.attr)
        elif (
            isinstance(child, ast.Subscript)
            and isinstance(child.slice, ast.Constant)
            and isinstance(child.slice.value, str)
            and SECRET_ENV_KEY.search(child.slice.value)
        ):
            found.add(f"[{child.slice.value}]")  # for example env["GARMIN_SYNC_GARMIN_TOKENS"]
    return found


# Calls that merely reformat their input: a secret that goes in comes out. Any other call is
# assumed to transform it into something else (a status, a count, a scope name).
_PASS_THROUGH = {"str", "repr", "format", "dumps", "bytes", "bytearray", "list", "dict", "tuple"}
_PASS_THROUGH |= {"join", "encode", "decode", "strip", "lower", "upper", "replace"}


def _carries_secret(node: ast.AST, names: set[str]) -> bool:
    """Whether the value of ``node`` is (or embeds) one of the secret-carrying names."""
    if isinstance(node, ast.Call):
        if _called_name(node) not in _PASS_THROUGH:
            return False
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        return any(_carries_secret(a, names) for a in node.args) or (
            receiver is not None and _carries_secret(receiver, names)
        )
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Attribute):
        return node.attr in names or _carries_secret(node.value, names)
    if isinstance(node, ast.Subscript):
        key = node.slice
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and SECRET_ENV_KEY.search(key.value)
        ):
            return True
        return _carries_secret(node.value, names)
    return any(_carries_secret(child, names) for child in ast.iter_child_nodes(node))


def leaks_in(source: str) -> list[str]:
    """Places where a secret-carrying name flows into output, an exception message or a log call.

    Per function it follows simple aliases (``x = access_token`` makes ``x`` sensitive too).
    """
    leaks = []
    for function in ast.walk(ast.parse(source)):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef | ast.Module):
            continue
        names = set(SENSITIVE_NAMES)
        for node in ast.walk(function):
            if isinstance(node, ast.Assign) and _carries_secret(node.value, names):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _called_name(node) in OUTPUT_CALLS:
                exposed = node.args
            elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                exposed = node.exc.args
            else:
                continue
            found = set()
            for argument in exposed:
                found |= _sensitive_names_in(argument, frozenset(names - SENSITIVE_NAMES))
            if found:
                leaks.append(f"line {node.lineno}: {sorted(found)}")
    return leaks


def test_secret_variables_are_never_passed_to_output_logging_or_exceptions() -> None:
    """Variables holding secrets must not flow into output; prose that says 'password' is fine."""
    hits = [
        f"{path.relative_to(ROOT)} {leak}"
        for path in (ROOT / "src").rglob("*.py")
        for leak in leaks_in(path.read_text())
    ]
    assert not hits, hits


@pytest.mark.parametrize(
    "leaky",
    [
        'print(f"token is {access_token}")',
        'log.info("x", client.client_secret)',
        "x = access_token\nprint(x)",
        'raise RuntimeError(f"bad token {refresh_token}")',
        'print(env["GARMIN_SYNC_GARMIN_TOKENS"])',
        "sys.stderr.write(token_json)",
        "_emit(body, False, fields)",
    ],
)
def test_the_output_check_would_catch_a_leak(leaky: str) -> None:
    assert leaks_in(leaky)


def test_the_output_check_leaves_prose_alone() -> None:
    assert not leaks_in('print("Logged in (the password is not stored)")')
    assert not leaks_in('raise ValueError("token file is unreadable")')


# -- network egress ---------------------------------------------------------------------------

# Every host the source mentions. The cloud routine's network allow-list is exactly the first
# four (plus Garmin's two API hosts, which live inside python-garminconnect), so a new host
# in the source is a change to what the routine may reach and must be a conscious one.
ALLOWED_HOSTS = {
    "health.googleapis.com",  # the API
    "oauth2.googleapis.com",  # token endpoint
    "accounts.google.com",  # consent screen
    "www.google.com",  # the redirect target of the consent flow
    "www.googleapis.com",  # scope names (never contacted)
}


def test_the_source_only_mentions_known_hosts() -> None:
    hosts = {
        match.group(1)
        for path in (ROOT / "src").rglob("*.py")
        for match in re.finditer(r"https?://([A-Za-z0-9.\-]+)", path.read_text())
    }
    assert hosts <= ALLOWED_HOSTS, sorted(hosts - ALLOWED_HOSTS)


def _receiver(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        target = func.value
        return target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
    return ""


def test_every_google_request_refuses_redirects() -> None:
    """A redirect could carry the bearer token or the client secret to another host."""
    methods = {"get", "post", "put", "patch", "delete", "request"}
    missing = []
    for path in (ROOT / "src" / "garmin_sync").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and _called_name(node) in methods):
                continue
            receiver = _receiver(node).lower()
            if receiver != "requests" and "http" not in receiver and "session" not in receiver:
                continue  # not an HTTP client call
            refuses = any(
                kw.arg == "allow_redirects"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is False
                for kw in node.keywords
            )
            if not refuses:
                missing.append(f"{path.name}:{node.lineno}")
    assert not missing, missing


# -- secrets and the command line -------------------------------------------------------------


def test_no_command_line_option_takes_a_secret() -> None:
    """Arguments are visible to other users and end up in shell history."""
    from garmin_sync.cli import build_parser

    options: list[str] = []

    def collect(parser) -> None:
        for action in parser._actions:  # noqa: SLF001 - introspecting argparse
            options.extend(action.option_strings)
            for sub in getattr(action, "choices", None) or {}:
                child = action.choices[sub] if isinstance(action.choices, dict) else None
                if child is not None:
                    collect(child)

    collect(build_parser())
    forbidden = re.compile(r"password|secret|token|redirect|code|credential|mfa", re.I)
    assert not [o for o in options if forbidden.search(o)], sorted(set(options))


def test_secret_carrying_objects_hide_their_values_in_reprs() -> None:
    from garmin_sync.routine import RoutineConfig

    config = RoutineConfig(
        garmin_tokens="TOKENS-SENTINEL",
        google_client_id="id",
        google_client_secret="SECRET-SENTINEL",  # pragma: allowlist secret
        google_refresh_token="REFRESH-SENTINEL",
        is_cn=False,
        apply=True,
        days=3,
        max_writes=50,
    )
    text = repr(config) + str(config)
    assert not any(
        word in text for word in ("TOKENS-SENTINEL", "SECRET-SENTINEL", "REFRESH-SENTINEL")
    )


def test_dependencies_have_upper_bounds_and_the_lockfile_is_committed() -> None:
    text = (ROOT / "pyproject.toml").read_text()
    assert re.search(r'"garminconnect==[0-9.]+"', text)  # unofficial upstream: pinned exactly
    for dependency in ("curl_cffi", "requests"):
        assert re.search(rf'"{dependency}>=[0-9.]+,<[0-9.]+"', text), dependency
    assert (ROOT / "uv.lock").exists()
    assert "exclude-newer" in text  # a cooling-off period for freshly published releases


def test_the_redirect_check_would_catch_a_bare_requests_call() -> None:
    """The scanner must not be fooled by a module-level `requests.post(...)` with no kwarg."""
    source = ast.parse("requests.post(url, data=body)\nhttp.get(url, allow_redirects=False)")
    calls = [n for n in ast.walk(source) if isinstance(n, ast.Call)]
    assert [_receiver(c) for c in calls] == ["requests", "http"]
