"""The ``auth`` command group: logging in to Garmin and Google, and checking both.

Secrets only ever pass through the user's own terminal: passwords, MFA codes and the Google
redirect address are read with hidden prompts, never from argv, and nothing here prints a
token, a password or an authorization code.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

from garmin_sync import usertime
from garmin_sync.errors import CliError
from garmin_sync.garmin_source import (
    REGION_FILENAME,
    TOKEN_FILENAME,
    login_from_tokens,
    login_interactive,
)
from garmin_sync.paths import AppHome, read_json, write_private_json, write_private_text
from garmin_sync.routine import (
    ENV_GARMIN_IS_CN,
    ENV_GARMIN_TOKENS,
    ENV_GOOGLE_CLIENT_ID,
    ENV_GOOGLE_CLIENT_SECRET,
    ENV_GOOGLE_REFRESH_TOKEN,
)
from garmin_sync.sinks import google_auth
from garmin_sync.sinks.google_client import GoogleClient

# The variables written to the cloud-env file. Only these names are ever shown, never the values.
CLOUD_ENV_NAMES = (
    usertime.ENV_TIMEZONE,
    ENV_GARMIN_TOKENS,
    ENV_GARMIN_IS_CN,
    ENV_GOOGLE_CLIENT_ID,
    ENV_GOOGLE_CLIENT_SECRET,
    ENV_GOOGLE_REFRESH_TOKEN,
)


def _require_terminal(what: str) -> None:
    if not sys.stdin.isatty():
        raise CliError(
            f"`{what}` needs an interactive terminal; run it yourself, not through a tool"
        )


def garmin_login(home: AppHome, *, is_cn: bool, log: Any) -> None:
    """Interactive Garmin login (email, hidden password, hidden MFA code)."""
    _require_terminal("auth garmin")
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (hidden): ")
    login_interactive(
        home,
        email,
        password,
        prompt_mfa=lambda: getpass.getpass("Garmin MFA code (hidden): ").strip(),
        is_cn=is_cn,
    )
    log("Logged in to Garmin. Tokens saved (the password is not stored).")


def garmin_check(home: AppHome) -> str:
    """Verify the saved Garmin session with one cheap call; raises on any problem."""
    login_from_tokens(home).probe()
    return "Garmin session is valid"


def google_init(home: AppHome, client_file: str, *, log: Any) -> None:
    """Validate a downloaded OAuth client JSON and keep a private copy of it."""
    path = Path(client_file).expanduser()
    if not path.is_file():
        raise CliError(f"file not found: {path}")
    google_auth.load_client(path)  # raises with a precise message if it is wrong
    home.ensure()
    write_private_json(home.google_client_file, read_json(path))
    log(f"Saved a private copy to {home.google_client_file}. You can delete {path} now.")


def google_login(home: AppHome, *, from_stdin: bool, log: Any) -> None:
    """Two steps in one command: print the consent URL, then take the redirected address."""
    home.ensure()
    client = google_auth.load_client(home.google_client_file)
    url = google_auth.start_authorization(client, home.google_pending_file)
    log("Open this address, approve every permission, then copy the FULL address of the page")
    log("you land on (it starts with https://www.google.com/?code=...):\n")
    print(url)
    if from_stdin:
        redirected = sys.stdin.readline()
    else:
        _require_terminal("auth google login")
        redirected = getpass.getpass("\nPaste the address here (hidden): ")
    token = google_auth.finish_authorization(
        client, home.google_pending_file, home.google_token_file, redirected, requests.Session()
    )
    missing = token.missing_scopes()
    log(
        "Google login complete."
        + (f" Missing permissions: {', '.join(missing)}" if missing else "")
    )


def google_check(home: AppHome) -> str:
    """Refresh the saved token and read the account identity; raises on any problem."""
    client = google_auth.load_client(home.google_client_file)
    http = requests.Session()
    auth = google_auth.GoogleAuth(client, google_auth.FileTokenStore(home.google_token_file), http)
    GoogleClient(auth, http).identity()
    missing = auth.token().missing_scopes()
    return "Google login works" + (
        f"; missing permissions: {', '.join(missing)}" if missing else ""
    )


def export_cloud_env(home: AppHome, *, log: Any) -> None:
    """Write the cloud routine's secrets to a private file; print only where it is.

    The file is for pasting into the routine environment's variable settings. Its contents
    never reach stdout, so a tool or chat session wrapping this command never sees them.
    """
    _require_terminal("auth export-cloud-env")
    token_file = home.garmin_token_dir / TOKEN_FILENAME
    if not token_file.exists():
        raise CliError("not logged in to Garmin; run `garmin-sync auth garmin` first")
    client = google_auth.load_client(home.google_client_file)
    google_token = google_auth.FileTokenStore(home.google_token_file).load()
    if google_token is None:
        raise CliError("not logged in to Google; run `garmin-sync auth google login` first")

    # Only a zone the user has stated counts: the machine's own zone is not evidence of theirs.
    zone = usertime.resolve_zone(
        os.environ, usertime.read_saved_zone(home.settings_file), use_system=False
    )
    if zone is None:
        raise CliError(f"{usertime.NOT_SET}. {usertime.ASK}, then try again")

    region_file = home.garmin_token_dir / REGION_FILENAME
    is_cn = bool(read_json(region_file).get("is_cn")) if region_file.exists() else False
    values = {
        usertime.ENV_TIMEZONE: zone.name,
        ENV_GARMIN_TOKENS: json.dumps(read_json(token_file), separators=(",", ":")),
        ENV_GARMIN_IS_CN: "1" if is_cn else "0",
        ENV_GOOGLE_CLIENT_ID: client.client_id,
        ENV_GOOGLE_CLIENT_SECRET: client.client_secret,
        ENV_GOOGLE_REFRESH_TOKEN: google_token.refresh_token,
    }
    write_private_text(home.cloud_env_file, "".join(f"{k}={v}\n" for k, v in values.items()))
    log(f"Wrote {home.cloud_env_file} (owner-only) with: {', '.join(CLOUD_ENV_NAMES)}")
    log("Paste its lines into the routine environment's variables, then delete the file.")
