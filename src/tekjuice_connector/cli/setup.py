"""CLI: ``tekjuice setup`` command.

Runs the connector-owned S2S setup against an already-running backend that
serves the receiver (``--route-path``), keeping the receiver secret in the
deployment's env file (``--env-file``) or a secret file (``--secret-file``).
Without either, secrets come from the environment and are not persisted.

Never prints the receiver secret or the connector key: output is the stage
report, the effective endpoint URL and the secret's fingerprint.

Exit codes: 0 ready, 1 a stage failed, 2 configuration invalid, 3 waiting
(e.g. the Data Engine is still verifying, or the backend must be restarted).
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from tekjuice_connector.exceptions.base import TekJuiceConnectorError
from tekjuice_connector.logging.logger import configure_logging

_CONNECTOR_KEYS = ("TEKJUICE_WEBSITE_ID", "TEKJUICE_CONNECTOR_KEY", "TEKJUICE_DATA_ENGINE_URL", "TEKJUICE_API_BASE_URL")


def run_setup(
    *,
    env_file: Optional[str] = None,
    secret_file: Optional[str] = None,
    api_base_url: Optional[str] = None,
    route_path: Optional[str] = None,
    public_content_url: Optional[str] = None,
    rotate: bool = False,
    rollback_rotation: bool = False,
    allow_endpoint_change: bool = False,
    as_json: bool = False,
) -> int:
    configure_logging()
    import os

    from tekjuice_connector import TekJuiceConnector
    from tekjuice_connector.setup import EnvFileSecretStore, EnvironmentSecretStore, FileSecretStore

    if env_file and secret_file:
        print("Use either --env-file or --secret-file, not both.", file=sys.stderr)
        return 2
    store = (EnvFileSecretStore(env_file) if env_file else FileSecretStore(secret_file) if secret_file
             else EnvironmentSecretStore())

    # Connector settings may live in the same env file (never exported), or
    # in secret files (TEKJUICE_CONNECTOR_KEY_FILE, as the sidecar uses).
    values = dict(os.environ)
    if not values.get("TEKJUICE_CONNECTOR_KEY") and values.get("TEKJUICE_CONNECTOR_KEY_FILE"):
        with open(values["TEKJUICE_CONNECTOR_KEY_FILE"], encoding="utf-8") as handle:
            values["TEKJUICE_CONNECTOR_KEY"] = handle.read().strip()
    if env_file:
        values.update({k: v for k, v in store._values().items() if k in _CONNECTOR_KEYS})  # noqa: SLF001
    try:
        connector = TekJuiceConnector(
            website_id=values.get("TEKJUICE_WEBSITE_ID"),
            connector_key=values.get("TEKJUICE_CONNECTOR_KEY"),
            data_engine_url=values.get("TEKJUICE_DATA_ENGINE_URL"),
        )
    except TekJuiceConnectorError as exc:
        print(f"Configuration invalid: {exc}", file=sys.stderr)
        return 2

    result = connector.setup(
        secret_store=store,
        api_base_url=api_base_url or values.get("TEKJUICE_API_BASE_URL"),
        route_path=route_path,
        public_content_url=public_content_url,
        rotate=rotate,
        rollback_rotation=rollback_rotation,
        allow_endpoint_change=allow_endpoint_change,
    )
    if as_json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        for stage in result.stages:
            print(f"[{stage.status:7}] {stage.stage.value}: {stage.detail}")
            if stage.status != "passed" and stage.remediation:
                print(f"          next: {stage.remediation}")
        print(f"\nEndpoint: {result.endpoint_url or 'not established'}")
        if result.receiver_secret_fingerprint:
            print(f"Receiver secret fingerprint: {result.receiver_secret_fingerprint} (the secret is not shown)")
        print(f"Result: {result.code.value if result.code else 'unknown'}")
    if result.ready:
        return 0
    failed = result.failed_stage
    return 3 if (result.restart_required or (failed and failed.status == "pending")) else 1
