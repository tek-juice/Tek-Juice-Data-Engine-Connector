"""Publication connection registration sent with the connector handshake."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlsplit

from tekjuice_connector.exceptions.configuration import (
    ConfigurationError,
    MissingConfigurationError,
)
from tekjuice_connector.publication.contract import SUPPORTED_CONTRACT_VERSIONS
from tekjuice_connector.publication.receiver import (
    MIN_RECEIVER_SECRET_LENGTH,
    PublicationReceiver,
)
from tekjuice_connector.security.secrets import SecretStr

ENV_PUBLICATION_ENDPOINT_URL = "TEKJUICE_PUBLICATION_ENDPOINT_URL"


@dataclass(frozen=True)
class PublicationRegistration:
    """Where and how the Data Engine publishes to this backend.

    Build it from the mounted receiver so the advertised capabilities are
    exactly what the receiver supports::

        registration = PublicationRegistration.from_receiver(
            receiver,
            endpoint_url="https://api.example.com/tekjuice/publish",
        )
        connector = TekJuiceConnector(publication=registration)

    The receiver secret is sent to the Data Engine only inside the
    authenticated handshake; it is redacted from ``repr`` and logs.

    ``public_content_url`` (optional) is for client-rendered sites whose
    pages are an empty HTML shell: an anonymous HTTPS URL template containing
    ``{slug}`` that serves the published content the page renders, e.g.
    ``https://www.example.com/api/content/{slug}``. The Data Engine fetches it
    to prove published content is publicly served.
    """

    endpoint_url: str
    receiver_secret: SecretStr
    capabilities: Tuple[str, ...]
    contract_versions: Tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(SUPPORTED_CONTRACT_VERSIONS))
    )
    framework: Optional[str] = None
    public_content_url: Optional[str] = None

    def __post_init__(self) -> None:
        parts = urlsplit(self.endpoint_url or "")
        if parts.scheme != "https" or not parts.hostname:
            raise ConfigurationError(
                "Publication endpoint URL must be an absolute https URL"
            )
        if parts.username or parts.password:
            raise ConfigurationError(
                "Publication endpoint URL must not contain credentials"
            )
        if self.public_content_url is not None:
            content = urlsplit(self.public_content_url)
            if content.scheme != "https" or not content.hostname:
                raise ConfigurationError("public_content_url must be an absolute https URL")
            if "{slug}" not in self.public_content_url:
                raise ConfigurationError("public_content_url must contain the {slug} placeholder")
        if len(self.receiver_secret.reveal()) < MIN_RECEIVER_SECRET_LENGTH:
            raise ConfigurationError(
                f"Receiver secret must be at least {MIN_RECEIVER_SECRET_LENGTH} characters"
            )

    @classmethod
    def from_receiver(
        cls,
        receiver: PublicationReceiver,
        *,
        endpoint_url: Optional[str] = None,
        framework: Optional[str] = None,
        public_content_url: Optional[str] = None,
    ) -> "PublicationRegistration":
        """Describe a mounted receiver. ``endpoint_url`` falls back to
        ``TEKJUICE_PUBLICATION_ENDPOINT_URL``."""
        endpoint_url = endpoint_url or os.environ.get(ENV_PUBLICATION_ENDPOINT_URL)
        if not endpoint_url:
            raise MissingConfigurationError(ENV_PUBLICATION_ENDPOINT_URL)

        if framework is None:
            from tekjuice_connector.adapters.detect import detect_framework

            framework = detect_framework()

        return cls(
            endpoint_url=endpoint_url,
            receiver_secret=receiver.receiver_secret,
            capabilities=tuple(sorted(receiver.capabilities)),
            contract_versions=tuple(sorted(receiver.contract_versions)),
            framework=framework,
            public_content_url=public_content_url,
        )

    def to_handshake_dict(self) -> Dict[str, Any]:
        body = {
            "endpoint_url": self.endpoint_url,
            "auth_scheme": "bearer",
            "receiver_secret": self.receiver_secret.reveal(),
            "contract_versions": list(self.contract_versions),
            "capabilities": list(self.capabilities),
            "framework": self.framework,
        }
        if self.public_content_url:
            body["public_content_url"] = self.public_content_url
        return body

    def __repr__(self) -> str:
        return (
            f"PublicationRegistration(endpoint_url={self.endpoint_url!r}, "
            f"capabilities={list(self.capabilities)!r}, framework={self.framework!r})"
        )
