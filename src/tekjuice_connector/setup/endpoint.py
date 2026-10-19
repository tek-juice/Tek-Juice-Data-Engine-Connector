"""The effective publication endpoint URL.

``TEKJUICE_PUBLICATION_ENDPOINT_URL`` is the backend's public API base URL
plus the route the receiver is mounted on. The base URL must come from the
backend's own configuration; the website's marketing domain is not evidence
of where its API is served, so it is never used.

Sources, in order:

1. ``api_base_url`` passed to the setup;
2. ``TEKJUICE_API_BASE_URL`` in the environment;
3. the framework's configured public URL (FastAPI ``servers``, Flask
   ``TEKJUICE_API_BASE_URL`` / ``SERVER_NAME``, Django
   ``settings.TEKJUICE_API_BASE_URL``);
4. a previously stored ``TEKJUICE_PUBLICATION_ENDPOINT_URL`` (reused whole).

The result must satisfy the Data Engine's rules (https, public DNS host, no
IP, default port, no credentials or fragment) and is then proven by a
self-check through the public URL before it is registered.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from tekjuice_connector.exceptions.configuration import ConfigurationError

ENV_API_BASE_URL = "TEKJUICE_API_BASE_URL"
DEFAULT_ROUTE_PATH = "/tekjuice/publish"


class EndpointError(ConfigurationError):
    """The publication endpoint URL is missing or not acceptable."""


@dataclass(frozen=True)
class EndpointPlan:
    url: str
    source: str  # where the base URL (or the whole URL) came from
    route_path: str


def _check_host(parts, what: str) -> None:
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https":
        raise EndpointError(f"{what} must use https")
    if not host or "." not in host or host == "localhost" or host.endswith(".local"):
        raise EndpointError(f"{what} must use a public DNS hostname")
    if host.replace(".", "").isdigit() or ":" in host:
        raise EndpointError(f"{what} must use a hostname, not an IP address")
    if parts.username or parts.password:
        raise EndpointError(f"{what} must not contain credentials")
    if parts.port not in (None, 443):
        raise EndpointError(f"{what} must use the default HTTPS port")
    if parts.fragment:
        raise EndpointError(f"{what} must not contain a fragment")
    if parts.query:
        raise EndpointError(f"{what} must not contain a query string")


def validate_endpoint_url(url: str) -> str:
    """The Data Engine's endpoint rules (services/onboarding/publication_connection)."""
    url = str(url or "").strip()
    if "<" in url or ">" in url or "{" in url:
        raise EndpointError("Publication endpoint URL is a placeholder, not a real URL")
    _check_host(urlsplit(url), "Publication endpoint URL")
    return url


def validate_base_url(url: str) -> str:
    url = str(url or "").strip()
    if "<" in url or ">" in url or "{" in url:
        raise EndpointError("API base URL is a placeholder, not a real URL")
    _check_host(urlsplit(url), "API base URL")
    return url.rstrip("/")


def join_endpoint(base_url: str, route_path: str) -> str:
    base = urlsplit(validate_base_url(base_url))
    route = "/" + route_path.strip().lstrip("/")
    path = base.path.rstrip("/") + route
    return validate_endpoint_url(urlunsplit(("https", base.netloc, path, "", "")))


def plan_endpoint(
    *,
    route_path: Optional[str],
    api_base_url: Optional[str] = None,
    framework_base: Optional[Tuple[str, str]] = None,
    stored_endpoint_url: Optional[str] = None,
    environ: Optional[dict] = None,
) -> EndpointPlan:
    """The endpoint URL and the evidence it rests on.

    Raises :class:`EndpointError` when no source gives an acceptable URL.
    """
    env = os.environ if environ is None else environ
    route = route_path or (urlsplit(stored_endpoint_url).path if stored_endpoint_url else None) \
        or DEFAULT_ROUTE_PATH

    candidates = [
        (api_base_url, "api_base_url argument"),
        (env.get(ENV_API_BASE_URL), f"{ENV_API_BASE_URL} environment variable"),
        framework_base or (None, ""),
    ]
    for base, source in candidates:
        if base:
            return EndpointPlan(url=join_endpoint(base, route), source=source, route_path=route)
    if stored_endpoint_url:
        url = validate_endpoint_url(stored_endpoint_url)
        if route_path and urlsplit(url).path.rstrip("/") != route.rstrip("/"):
            raise EndpointError(
                f"The stored endpoint {url} does not use the receiver's route {route}; "
                f"set {ENV_API_BASE_URL} so the endpoint can be rebuilt"
            )
        return EndpointPlan(url=url, source="stored TEKJUICE_PUBLICATION_ENDPOINT_URL", route_path=route)
    raise EndpointError(
        "The backend's public API base URL is not configured. Set "
        f"{ENV_API_BASE_URL}=https://<your-api-host> (the host the Data Engine can reach, which may differ "
        "from the website's domain), or pass api_base_url to the setup."
    )
