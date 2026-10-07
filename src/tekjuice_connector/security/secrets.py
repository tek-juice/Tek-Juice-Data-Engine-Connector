"""Utilities for handling secrets safely throughout the package.

Rules enforced here:
- Connector keys are never returned as plain strings in public methods.
- ``SecretStr`` wraps a secret value and prevents it from appearing in
  repr(), str(), or format() calls.
- The raw value is only accessible via an explicit ``.reveal()`` call so
  that accidental string interpolation cannot leak it.
"""

from __future__ import annotations

from tekjuice_connector.security.redaction import REDACTED_MARKER


class SecretStr:
    """A string wrapper that redacts its value from all standard output.

    Usage::

        key = SecretStr("sk-abc123...")
        print(key)          # prints <redacted>
        print(repr(key))    # prints SecretStr(<redacted>)
        raw = key.reveal()  # the only way to get the real value
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    # ------------------------------------------------------------------
    # Never expose the value via standard Python protocols
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return f"SecretStr({REDACTED_MARKER})"

    def __str__(self) -> str:
        return REDACTED_MARKER

    def __format__(self, format_spec: str) -> str:  # noqa: D105
        return REDACTED_MARKER

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretStr):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    # ------------------------------------------------------------------
    # Explicit access
    # ------------------------------------------------------------------

    def reveal(self) -> str:
        """Return the raw secret value.

        This method name is intentionally explicit so that callers cannot
        accidentally call it through normal string operations.
        """
        return self._value

    @property
    def hint(self) -> str:
        """Return the first four characters followed by '...' as a hint."""
        if len(self._value) >= 4:
            return self._value[:4] + "..."
        return REDACTED_MARKER
