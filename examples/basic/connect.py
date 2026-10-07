"""Basic example — connect and check READY status.

Run with:
    export TEKJUICE_WEBSITE_ID=your-website-id
    export TEKJUICE_CONNECTOR_KEY=your-connector-key
    export TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io
    python examples/basic/connect.py
"""

from __future__ import annotations

import sys

from tekjuice_connector import (
    TekJuiceConnector,
    InvalidCredentialsError,
    MissingConfigurationError,
    RetryExhaustedError,
    ValidationError,
)


def main() -> int:
    # Connector reads configuration from environment variables.
    try:
        connector = TekJuiceConnector()
    except MissingConfigurationError as exc:
        print(f"Missing configuration: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 1

    print(f"Connecting to {connector.settings.credentials.data_engine_url} …")

    try:
        result = connector.connect()
    except InvalidCredentialsError:
        print("Authentication failed. Check TEKJUICE_CONNECTOR_KEY.", file=sys.stderr)
        return 2
    except RetryExhaustedError as exc:
        print(f"Connection failed after retries: {exc}", file=sys.stderr)
        return 3

    if result.ready:
        print("Connector is READY.")
        print(f"  Website ID:        {result.website_id}")
        print(f"  Domain:            {result.domain}")
        print(f"  Onboarding status: {result.onboarding_status}")
        return 0

    print(f"Connector not READY. Status: {result.onboarding_status}", file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
