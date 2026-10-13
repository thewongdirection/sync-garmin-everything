"""Neutralize untrusted text before it is printed, logged or put in a report.

Anything that originates from Garmin or Google (activity names, error messages, ids,
JSON keys) is controlled by a third party. This tool's output is read by people and by
AI agents, so such text must not be able to smuggle in terminal escape sequences,
fake line breaks that imitate other output, invisible characters that carry hidden
instructions, or unbounded amounts of text.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# CSI escape sequences (colour, cursor movement, ...), removed before control characters.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Characters that never render as visible text but can carry a message to a model: format characters
# (zero-width and joiner characters, bidirectional marks, the "tag" block that mirrors ASCII), the
# variation selectors, and similar fillers. They are removed outright, so a word they split is
# rejoined and nothing is left behind to signal their position.
_DELETED_CATEGORIES = frozenset({"Cf", "Cs", "Co", "Cn"})
_DELETED_EXTRA = frozenset(
    [0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x180F, 0x3164, 0xFFA0, 0x2800]
    + list(range(0x180B, 0x180E))
    + list(range(0xFE00, 0xFE10))
    + list(range(0xE0100, 0xE01F0))
)
# Control characters and line/paragraph separators are replaced by a space, so they still separate
# words but cannot start a new line.
_SPACED_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})

DEFAULT_LIMIT = 160
# Work on a bounded prefix so a megabyte of hostile text costs a bounded amount of effort.
_WORK_FACTOR = 8


def _scrub(char: str) -> str:
    if ord(char) in _DELETED_EXTRA:
        return ""
    category = unicodedata.category(char)
    if category in _DELETED_CATEGORIES:
        return ""
    return " " if category in _SPACED_CATEGORIES else char


def clean(value: Any, limit: int = DEFAULT_LIMIT) -> str:
    """``value`` as single-line printable text of at most ``limit`` characters."""
    text = _ANSI.sub("", str(value)[: max(limit, 1) * _WORK_FACTOR])
    text = "".join(map(_scrub, text))
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: max(limit - 1, 0)] + "\u2026"
    return text
