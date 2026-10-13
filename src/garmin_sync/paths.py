"""Local state locations and permission-safe file helpers.

Everything this tool persists (Garmin tokens, Google tokens, the raw health-data
archive, exports) is sensitive. It lives under one private directory outside the
repository, is created owner-only (0700 dirs / 0600 files) and is written atomically.

The state directory is only ever *adopted* when it is empty or was created by this tool
(it carries a marker file), so pointing ``--home`` at, say, ``~/Documents`` cannot have
its permissions rewritten.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HOME_ENV = "GARMIN_SYNC_HOME"
DEFAULT_DIRNAME = ".garmin-sync"
HOME_MARKER = ".garmin-sync-home"

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class UnsafeHomeError(Exception):
    """The chosen state directory is not safe to adopt; the message says why."""


@dataclass(frozen=True)
class AppHome:
    """Resolved locations for all local state."""

    root: Path

    @classmethod
    def resolve(cls, override: str | os.PathLike[str] | None = None) -> AppHome:
        """Pick the state dir: explicit override, then $GARMIN_SYNC_HOME, then ~/.garmin-sync."""
        chosen = override or os.environ.get(HOME_ENV)
        root = Path(chosen).expanduser() if chosen else Path.home() / DEFAULT_DIRNAME
        return cls(root=root)

    @property
    def garmin_token_dir(self) -> Path:
        return self.root / "garmin"

    @property
    def google_client_file(self) -> Path:
        return self.root / "google_client.json"

    @property
    def google_token_file(self) -> Path:
        return self.root / "google_token.json"

    @property
    def google_pending_file(self) -> Path:
        return self.root / "google_oauth_pending.json"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def archive_file(self) -> Path:
        return self.root / "archive.sqlite3"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def fit_dir(self) -> Path:
        return self.root / "fit"

    @property
    def cloud_env_file(self) -> Path:
        return self.root / "cloud-env.txt"

    def ensure(self) -> None:
        """Create or adopt the state directory tree, owner-only."""
        self._adopt_root()
        for directory in (self.garmin_token_dir, self.exports_dir, self.fit_dir):
            ensure_private_dir(directory, tighten_existing=True)

    def _adopt_root(self) -> None:
        root = self.root
        if root.is_symlink():
            raise UnsafeHomeError(f"{root} is a symbolic link; choose a real directory")
        if root.exists():
            if not root.is_dir():
                raise UnsafeHomeError(f"{root} exists and is not a directory")
            if root.stat().st_uid != os.getuid():
                raise UnsafeHomeError(f"{root} is owned by another user")
            if not (root / HOME_MARKER).exists() and any(root.iterdir()):
                raise UnsafeHomeError(
                    f"{root} is not empty and was not created by garmin-sync; "
                    "refusing to change its permissions. Choose an empty or new directory."
                )
        ensure_private_dir(root, tighten_existing=True)
        marker = root / HOME_MARKER
        if not marker.exists():
            write_private_text(marker, "garmin-sync state directory\n")


def ensure_private_dir(path: Path, *, tighten_existing: bool = False) -> None:
    """Create ``path`` (and any missing parents) owner-only.

    A directory that already exists keeps its permissions unless ``tighten_existing`` is
    set, which callers use only for directories they own.
    """
    missing = []
    probe = path
    while not probe.exists():
        missing.append(probe)
        if probe.parent == probe:
            break
        probe = probe.parent
    for directory in reversed(missing):
        directory.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)
        _chmod_directory(directory)  # refuses a symlink that appeared in the meantime
    if tighten_existing and path not in missing:
        _chmod_directory(path)


def _chmod_directory(path: Path) -> None:
    """Make ``path`` owner-only, refusing a symlink or a directory that is not ours.

    The directory is opened without following links and changed through that handle, so
    swapping a symlink in between the checks and the change cannot redirect it.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise UnsafeHomeError(f"{path} is not a plain directory (is it a symbolic link?)") from exc
    try:
        if os.fstat(fd).st_uid != os.getuid():
            raise UnsafeHomeError(f"{path} is owned by another user")
        os.fchmod(fd, PRIVATE_DIR_MODE)
    finally:
        os.close(fd)


def write_private_bytes(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` with 0600 permissions."""
    ensure_private_dir(path.parent)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), PRIVATE_FILE_MODE)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def write_private_text(path: Path, text: str) -> None:
    write_private_bytes(path, text.encode("utf-8"))


def write_private_json(path: Path, payload: Any) -> None:
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_private(path: Path) -> bool:
    """True when ``path`` is not accessible to group or others."""
    return (path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)) == 0
