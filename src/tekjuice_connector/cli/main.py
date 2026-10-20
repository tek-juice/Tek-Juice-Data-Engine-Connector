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

    # serve (sidecar)
    subparsers.add_parser(
        "serve",
        help="Run the connector sidecar (S2S receiver for a backend in any language; env configuration).",
    )

    proxy_p = subparsers.add_parser(
        "proxy-config",
        help="Print nginx or Caddy configuration for the sidecar (from its environment configuration).",
    )
    flavour = proxy_p.add_mutually_exclusive_group()
    flavour.add_argument("--nginx", dest="flavour", action="store_const", const="nginx", default="nginx")
    flavour.add_argument("--caddy", dest="flavour", action="store_const", const="caddy")
    proxy_p.add_argument("--upstream", default="http://127.0.0.1:8787", help="Where the sidecar listens.")
    proxy_p.add_argument("--site-upstream", default="http://127.0.0.1:3000", help="Where your website is served.")
    proxy_p.add_argument("--template", help="Public URL template of articles (default: TEKJUICE_PUBLIC_URL_TEMPLATE).")

    content_types_p = subparsers.add_parser(
        "content-types",
        help="Show the backend's content types: discovered, validated, ready, and what is missing.",
    )
    content_types_p.add_argument("--json", action="store_true", help="Print the capability map as JSON.")

    subparsers.add_parser(
        "check-content-api",
        help="Non-mutating conformance checks of the backend's content API (sidecar configuration).",
    )

    # setup
    connect_p = subparsers.add_parser(
        "connect",
        help="Connect this server in one step: credentials, handshake, endpoint, verification (any language).",
    )
    connect_p.add_argument("--env-file", default=".env", help="Dotenv file with the credentials (default: .env).")
    connect_p.add_argument("--project-dir", default=".", help="The application's directory (default: current).")
    connect_p.add_argument("--yes", "-y", action="store_true", help="Accept the proposed addresses and changes.")

    setup_p = subparsers.add_parser(
        "setup",
        help="Set up S2S publication: receiver secret, endpoint URL, registration, verification.",
    )
    store = setup_p.add_mutually_exclusive_group()
    store.add_argument("--env-file", help="Dotenv file the deployment loads (secret written there, mode 0600).")
    store.add_argument("--secret-file", help="JSON secret file on a persistent volume.")
    setup_p.add_argument("--api-base-url", help="Public https base URL of the backend API "
                                                "(default: TEKJUICE_API_BASE_URL).")
    setup_p.add_argument("--route-path", help="Path the running backend serves the receiver on.")
    setup_p.add_argument("--public-content-url", help="https URL template with {slug} serving published content.")
    setup_p.add_argument("--rotate", action="store_true", help="Rotate the receiver secret (coordinated).")
    setup_p.add_argument("--rollback-rotation", action="store_true", help="Abandon a pending rotation.")
    setup_p.add_argument("--allow-endpoint-change", action="store_true",
                         help="Allow moving a verified registration to a different endpoint URL.")
    setup_p.add_argument("--json", action="store_true", help="Print the secret-free result as JSON.")

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

    if args.command == "proxy-config":
        from tekjuice_connector.cli.proxy_config import run_proxy_config
        return run_proxy_config(flavour=args.flavour, upstream=args.upstream, site_upstream=args.site_upstream,
                                template=args.template)

    if args.command == "content-types":
        from tekjuice_connector.cli.content_types import run_content_types
        return run_content_types(as_json=args.json)

    if args.command == "check-content-api":
        from tekjuice_connector.cli.check_content_api import run_check_content_api
        return run_check_content_api()

    if args.command == "serve":
        from tekjuice_connector.cli.serve import run_serve
        return run_serve()

    if args.command == "connect":
        from tekjuice_connector.connect.wizard import run_connect

        return run_connect(env_file=args.env_file, project_dir=args.project_dir, assume_yes=args.yes)

    if args.command == "setup":
        from tekjuice_connector.cli.setup import run_setup
        return run_setup(
            env_file=args.env_file, secret_file=args.secret_file, api_base_url=args.api_base_url,
            route_path=args.route_path, public_content_url=args.public_content_url, rotate=args.rotate,
            rollback_rotation=args.rollback_rotation, allow_endpoint_change=args.allow_endpoint_change,
            as_json=args.json,
        )

    parser.print_help()
    return 0


def cli_entry() -> None:
    """Console-script entry point registered in ``pyproject.toml``."""
    sys.exit(main())


if __name__ == "__main__":
    cli_entry()
