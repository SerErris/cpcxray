"""The session's error type, in its own module so session.py and its mixins can share it."""

from __future__ import annotations


class SessionError(ValueError):
    """A request the session cannot carry out (bad argument, missing file, ...)."""
