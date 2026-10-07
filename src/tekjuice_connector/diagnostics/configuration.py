"""Configuration diagnostic checks.

Validates the current configuration without performing any network I/O.
Returns structured results that can be incorporated into a diagnostic report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from tekjuice_connector.config.validation import (
    validate_connector_key,
    validate_data_engine_url,
    validate_website_id,
)
from tekjuice_connector.security.redaction import REDACTED_MARKER, safe_url
from tekjuice_connector.security.validation import assert_no_credentials_in_url


@dataclass
class ConfigCheck:
    """Result of a single configuration field check."""

    name: str
    ok: bool
    detail: str
    # Safe display value — never the raw secret.
    display_value: Optional[str] = None


@dataclass
class ConfigDiagnosticResult:
    """Aggregated result of all configuration checks."""

    checks: List[ConfigCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when every check passed."""
        return all(c.ok for c in self.checks)

    def summary(self) -> str:
        lines = ["Configuration diagnostic:"]
        for c in self.checks:
            status = "OK " if c.ok else "FAIL"
            val = f"  ({c.display_value})" if c.display_value else ""
            lines.append(f"  [{status}] {c.name}{val}: {c.detail}")
        return "\n".join(lines)


def check_configuration(
    website_id: Optional[str],
    connector_key: Optional[str],
    data_engine_url: Optional[str],
) -> ConfigDiagnosticResult:
    """Run all configuration checks and return a :class:`ConfigDiagnosticResult`.

    No network I/O is performed.  The connector key is never included in
    any display value or detail string.

    Parameters
    ----------
    website_id, connector_key, data_engine_url:
        Raw values to check (``None`` means absent).
    """
    result = ConfigDiagnosticResult()

    # --- website_id ---
    try:
        validated_wid = validate_website_id(website_id)
        result.checks.append(
            ConfigCheck(
                name="TEKJUICE_WEBSITE_ID",
                ok=True,
                detail="Present and valid.",
                display_value=validated_wid,
            )
        )
    except Exception as exc:
        result.checks.append(
            ConfigCheck(
                name="TEKJUICE_WEBSITE_ID",
                ok=False,
                detail=str(exc),
            )
        )

    # --- connector_key ---
    try:
        validate_connector_key(connector_key)
        result.checks.append(
            ConfigCheck(
                name="TEKJUICE_CONNECTOR_KEY",
                ok=True,
                detail="Present.",
                display_value=REDACTED_MARKER,  # never show the value
            )
        )
    except Exception as exc:
        result.checks.append(
            ConfigCheck(
                name="TEKJUICE_CONNECTOR_KEY",
                ok=False,
                detail=str(exc),
                display_value=REDACTED_MARKER,
            )
        )

    # --- data_engine_url ---
    try:
        validated_url = validate_data_engine_url(data_engine_url)
        assert_no_credentials_in_url(validated_url)
        result.checks.append(
            ConfigCheck(
                name="TEKJUICE_DATA_ENGINE_URL",
                ok=True,
                detail="Present and valid.",
                display_value=safe_url(validated_url),
            )
        )
    except Exception as exc:
        result.checks.append(
            ConfigCheck(
                name="TEKJUICE_DATA_ENGINE_URL",
                ok=False,
                detail=str(exc),
                display_value=safe_url(data_engine_url or ""),
            )
        )

    return result
