"""Errors that end a command with a message and no traceback."""

from __future__ import annotations


class CliError(Exception):
    """A user-facing failure; the message is printed as is."""
