"""Connector-owned S2S setup: receiver secret, publication endpoint,
registration with the Data Engine and evidence-based readiness.

Usage (in the backend, after creating the app and the receiver)::

    from tekjuice_connector import TekJuiceConnector
    from tekjuice_connector.setup import EnvFileSecretStore

    result = TekJuiceConnector().setup(
        receiver=receiver, app=app,
        secret_store=EnvFileSecretStore("/run/tekjuice/tekjuice.env"),
    )
    result.ready, result.endpoint_url, result.to_dict()

See docs/setup.md.
"""

from tekjuice_connector.setup.endpoint import (
    DEFAULT_ROUTE_PATH,
    ENV_API_BASE_URL,
    EndpointError,
    EndpointPlan,
    join_endpoint,
    plan_endpoint,
    validate_endpoint_url,
)
from tekjuice_connector.setup.frameworks import (
    FrameworkIntegration,
    RouteRegistrationUnavailable,
    register_integration,
    resolve_integration,
)
from tekjuice_connector.setup.secret_store import (
    ENV_PUBLICATION_ENDPOINT_URL,
    ENV_RECEIVER_SECRET,
    ENV_RECEIVER_SECRET_PENDING,
    EnvFileSecretStore,
    EnvironmentSecretStore,
    FileSecretStore,
    ReceiverSecrets,
    SecretPersistenceError,
    SecretStore,
)
from tekjuice_connector.setup.workflow import (
    ConnectorSetup,
    SetupCode,
    SetupResult,
    Stage,
    StageResult,
    generate_receiver_secret,
    receiver_secret_fingerprint,
)

__all__ = [
    "ConnectorSetup", "DEFAULT_ROUTE_PATH", "ENV_API_BASE_URL", "ENV_PUBLICATION_ENDPOINT_URL",
    "ENV_RECEIVER_SECRET", "ENV_RECEIVER_SECRET_PENDING", "EndpointError", "EndpointPlan", "EnvFileSecretStore",
    "EnvironmentSecretStore", "FileSecretStore", "FrameworkIntegration", "ReceiverSecrets",
    "RouteRegistrationUnavailable", "SecretPersistenceError", "SecretStore", "SetupCode", "SetupResult", "Stage",
    "StageResult", "generate_receiver_secret", "join_endpoint", "plan_endpoint", "receiver_secret_fingerprint",
    "register_integration", "resolve_integration", "validate_endpoint_url",
]
