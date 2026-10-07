"""Diagnostic report model.

:class:`DiagnosticReport` is the structured output produced by
:class:`~tekjuice_connector.diagnostics.diagnostic.Diagnostician`.
It can be rendered as plain text for the CLI or inspected programmatically.

Security guarantee: the connector key never appears anywhere in the report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tekjuice_connector.diagnostics.configuration import ConfigDiagnosticResult
from tekjuice_connector.diagnostics.connectivity import ConnectivityCheckResult
from tekjuice_connector.version import __version__


@dataclass
class DiagnosticReport:
    """Full diagnostic report.

    Attributes
    ----------
    config:
        Result of configuration validation checks.
    connectivity:
        Result of the live connectivity check, or ``None`` if not performed.
    """

    config: ConfigDiagnosticResult
    connectivity: Optional[ConnectivityCheckResult] = None

    @property
    def overall_ok(self) -> bool:
        """``True`` when configuration is valid and the Connector is READY."""
        if not self.config.ok:
            return False
        if self.connectivity is None:
            return False
        return self.connectivity.ready

    def to_text(self) -> str:
        """Render the report as human-readable plain text.

        The connector key is never included in the output.
        """
        lines: list[str] = [
            "=" * 60,
            f"Tek Juice Connector Diagnostics  (v{__version__})",
            "=" * 60,
            "",
        ]

        # Configuration section
        lines.append(self.config.summary())
        lines.append("")

        # Connectivity section
        if self.connectivity is not None:
            lines.append("Connectivity diagnostic:")
            lines.append(f"  {self.connectivity.summary()}")
        else:
            lines.append("Connectivity diagnostic: not performed.")

        lines.append("")

        # Overall verdict
        verdict = "READY" if self.overall_ok else "NOT READY"
        lines.append(f"Overall: {verdict}")
        lines.append("=" * 60)

        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"DiagnosticReport("
            f"config_ok={self.config.ok}, "
            f"connectivity_ok={self.connectivity.ok if self.connectivity else None}, "
            f"overall_ok={self.overall_ok})"
        )
