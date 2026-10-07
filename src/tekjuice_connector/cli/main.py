"""CLI entry point — ``tekjuice`` command.

Dispatches to subcommands:

    tekjuice status              Print current connection state (no network call)
    tekjuice test-connection     Live E2E handshake test
    tekjuice diagnostics         Full diagnostic report (config + connectivity)

Global flags:
    --verbose / -v               Enable verbose output
    --config-only                (diagnostics) Skip live connectivity check
    --version                    Print the package version and exit
    --help / -h                  Show help text

Uses only the standard library — no Click, Typer, or argparse extras.
"""

from __future__ import annotations

import argparse
import sys

from tekjuice_connector.version import __version__


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tekjuice",
        description=(
            "Tek Juice Data Engine Connector CLI.\n\n"
            "Reads configuration from environment variables:\n"
            "  TEKJUICE_WEBSITE_ID\n"
            "  TEKJUICE_CONNECTOR_KEY\n"
            "  TEKJUICE_DATA_ENGINE_URL"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"tekjuice-connector {__version__}",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output.",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # status
    status_p = subparsers.add_parser(
        "status",
        help="Print the current connection state (no network call).",
    )
    status_p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show full state details.",
    )

    # test-connection
    test_p = subparsers.add_parser(
        "test-connection",
        help="Perform a live E2E handshake and report the result.",
    )
    test_p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show full response details on success.",
    )

    # diagnostics
    diag_p = subparsers.add_parser(
        "diagnostics",
        help="Run the full diagnostic suite (configuration + connectivity).",
    )
    diag_p.add_argument(
        "--config-only",
        action="store_true",
        help="Validate configuration only; skip live connectivity check.",
    )

    return parser


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand.

    Returns
    -------
    int
        Exit code (0 = success).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "status":
        from tekjuice_connector.cli.status import run_status
        return run_status(verbose=getattr(args, "verbose", False))

    if args.command == "test-connection":
        from tekjuice_connector.cli.test_connection import run_test_connection
        return run_test_connection(verbose=getattr(args, "verbose", False))

    if args.command == "diagnostics":
        from tekjuice_connector.cli.diagnostics import run_diagnostics
        return run_diagnostics(config_only=getattr(args, "config_only", False))

    parser.print_help()
    return 0


def cli_entry() -> None:
    """Console-script entry point registered in ``pyproject.toml``."""
    sys.exit(main())


if __name__ == "__main__":
    cli_entry()
