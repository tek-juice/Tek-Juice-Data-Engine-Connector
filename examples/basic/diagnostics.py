"""Basic example — run diagnostics and print the report.

Run with:
    export TEKJUICE_WEBSITE_ID=your-website-id
    export TEKJUICE_CONNECTOR_KEY=your-connector-key
    export TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io
    python examples/basic/diagnostics.py
"""

from __future__ import annotations

import sys

from tekjuice_connector import TekJuiceConnector, TekJuiceConnectorError


def main() -> int:
    try:
        connector = TekJuiceConnector()
    except TekJuiceConnectorError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    # Run the full diagnostic suite (config + live connectivity).
    report = connector.diagnostics()
    print(report.to_text())

    return 0 if report.overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
