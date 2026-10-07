"""Diagnostic orchestrator.

:class:`Diagnostician` combines configuration and connectivity checks into
a single high-level object that the CLI and the public
``connector.diagnostics()`` API delegate to.
"""

from __future__ import annotations

from typing import Optional

from tekjuice_connector.config.settings import ConnectorSettings
from tekjuice_connector.connection.manager import ConnectionManager
from tekjuice_connector.diagnostics.configuration import (
    ConfigDiagnosticResult,
    check_configuration,
)
from tekjuice_connector.diagnostics.connectivity import (
    ConnectivityCheckResult,
    check_connectivity,
)
from tekjuice_connector.diagnostics.report import DiagnosticReport


class Diagnostician:
    """Runs all diagnostic checks and produces a :class:`DiagnosticReport`.

    Parameters
    ----------
    settings:
        The validated connector settings to inspect.
    manager:
        Optional connection manager.  When provided, a live connectivity
        check is included in the report.  When ``None``, connectivity is
        reported as unchecked.
    """

    def __init__(
        self,
        settings: ConnectorSettings,
        manager: Optional[ConnectionManager] = None,
    ) -> None:
        self._settings = settings
        self._manager = manager

    def run(self) -> DiagnosticReport:
        """Execute all checks and return a :class:`DiagnosticReport`."""
        creds = self._settings.credentials

        config_result: ConfigDiagnosticResult = check_configuration(
            website_id=creds.website_id,
            connector_key=creds.connector_key,
            data_engine_url=creds.data_engine_url,
        )

        connectivity_result: Optional[ConnectivityCheckResult] = None
        if self._manager is not None:
            connectivity_result = check_connectivity(self._manager)

        return DiagnosticReport(
            config=config_result,
            connectivity=connectivity_result,
        )

    @classmethod
    def from_settings(
        cls,
        settings: ConnectorSettings,
        *,
        include_connectivity: bool = True,
    ) -> "Diagnostician":
        """Build a :class:`Diagnostician` from a :class:`ConnectorSettings`.

        Parameters
        ----------
        settings:
            Validated connector settings.
        include_connectivity:
            When ``True`` (default) a :class:`ConnectionManager` is built
            and a live connectivity check will be performed on :meth:`run`.
            Pass ``False`` to limit diagnostics to configuration only.
        """
        manager: Optional[ConnectionManager] = None
        if include_connectivity and settings.credentials:
            manager = ConnectionManager.from_settings(settings)
        return cls(settings=settings, manager=manager)
