"""Framework integrations the setup can use.

Each integration answers, for an application object:

* ``matches(app)``: is this the framework's application?
* ``find_route(app)``: the path of an already-mounted receiver route, if any;
* ``mount(app, receiver, path)``: mount the receiver route, where the
  framework supports adding routes from code at this point; otherwise raise
  :class:`RouteRegistrationUnavailable` with the exact missing step;
* ``base_url(app)``: the public API base URL the application declares, as
  ``(url, source)``, or None.

Supported: FastAPI and Starlette (routes can be added at any time), Flask
(before the application has handled its first request), Django (routes live
in the URLconf, which is code: existing routes are found, new ones must be
added by the developer). Other frameworks can register a custom integration
with :func:`register_integration`.
"""

from __future__ import annotations

import sys
from typing import Any, List, Optional, Protocol, Tuple

FASTAPI_ROUTE_NAME = "tekjuice_receive_publication"
FLASK_ENDPOINT = "tekjuice_publication.receive_publication"


class RouteRegistrationUnavailable(RuntimeError):
    """The framework cannot have the route added by the connector."""


class FrameworkIntegration(Protocol):
    name: str

    def matches(self, app: Any) -> bool: ...

    def find_route(self, app: Any) -> Optional[str]: ...

    def mount(self, app: Any, receiver: Any, path: str) -> None: ...

    def base_url(self, app: Any) -> Optional[Tuple[str, str]]: ...


def _join_root(root: str, path: str) -> str:
    root = (root or "").rstrip("/")
    return f"{root}{path}" if root else path


class StarletteIntegration:
    """FastAPI / Starlette applications."""

    name = "fastapi"

    def matches(self, app: Any) -> bool:
        starlette = sys.modules.get("starlette.applications")
        return bool(starlette) and isinstance(app, starlette.Starlette)

    def find_route(self, app: Any) -> Optional[str]:
        root = getattr(app, "root_path", "") or ""
        for route in getattr(app, "routes", []):
            if getattr(route, "name", None) == FASTAPI_ROUTE_NAME:
                return _join_root(root, route.path)
        return None

    def mount(self, app: Any, receiver: Any, path: str) -> None:
        from tekjuice_connector.adapters.fastapi import create_router

        if not hasattr(app, "include_router"):
            raise RouteRegistrationUnavailable(
                "This is a plain Starlette application: add "
                "Mount/Route for tekjuice_connector.adapters.asgi.make_asgi_app(receiver) yourself"
            )
        app.include_router(create_router(receiver, path=path))

    def base_url(self, app: Any) -> Optional[Tuple[str, str]]:
        for server in getattr(app, "servers", None) or []:
            url = server.get("url") if isinstance(server, dict) else None
            if url and url.startswith("https://"):
                return url, "FastAPI app.servers"
        return None


class FlaskIntegration:
    name = "flask"

    def matches(self, app: Any) -> bool:
        flask = sys.modules.get("flask")
        return bool(flask) and isinstance(app, flask.Flask)

    def find_route(self, app: Any) -> Optional[str]:
        for rule in app.url_map.iter_rules():
            if rule.endpoint == FLASK_ENDPOINT or rule.endpoint.endswith(".receive_publication"):
                return _join_root(app.config.get("APPLICATION_ROOT") or "", rule.rule) \
                    if (app.config.get("APPLICATION_ROOT") or "/") != "/" else rule.rule
        return None

    def mount(self, app: Any, receiver: Any, path: str) -> None:
        from tekjuice_connector.adapters.flask import create_blueprint

        try:
            app.register_blueprint(create_blueprint(receiver, path=path))
        except AssertionError as exc:  # Flask refuses after the first request
            raise RouteRegistrationUnavailable(
                "Flask no longer accepts new routes (the app has handled requests): run the setup while "
                "creating the app, or register create_blueprint(receiver) yourself"
            ) from exc

    def base_url(self, app: Any) -> Optional[Tuple[str, str]]:
        configured = app.config.get("TEKJUICE_API_BASE_URL")
        if configured:
            return configured, "Flask config TEKJUICE_API_BASE_URL"
        server = app.config.get("SERVER_NAME")
        if server and app.config.get("PREFERRED_URL_SCHEME") == "https":
            root = (app.config.get("APPLICATION_ROOT") or "").rstrip("/")
            return f"https://{server}{root}", "Flask config SERVER_NAME"
        return None


class DjangoIntegration:
    """Django projects: pass ``django.conf.settings`` as the app."""

    name = "django"

    def matches(self, app: Any) -> bool:
        conf = sys.modules.get("django.conf")
        return bool(conf) and app is getattr(conf, "settings", None)

    def find_route(self, app: Any) -> Optional[str]:
        from django.urls import get_resolver

        def walk(patterns, prefix: str) -> Optional[str]:
            for pattern in patterns:
                route = prefix + str(pattern.pattern)
                if hasattr(pattern, "url_patterns"):
                    found = walk(pattern.url_patterns, route)
                    if found:
                        return found
                elif getattr(pattern.callback, "tekjuice_receiver", None) is not None:
                    return "/" + route.lstrip("^").rstrip("$").lstrip("/")
            return None

        return walk(get_resolver().url_patterns, "")

    def mount(self, app: Any, receiver: Any, path: str) -> None:
        raise RouteRegistrationUnavailable(
            "Django routes are declared in the URLconf: add "
            f"path({path.lstrip('/')!r}, tekjuice_connector.adapters.django.create_view(receiver)) "
            "to urls.py, deploy, then run the setup again"
        )

    def base_url(self, app: Any) -> Optional[Tuple[str, str]]:
        configured = getattr(app, "TEKJUICE_API_BASE_URL", None)
        return (configured, "Django settings.TEKJUICE_API_BASE_URL") if configured else None


_INTEGRATIONS: List[FrameworkIntegration] = [StarletteIntegration(), FlaskIntegration(), DjangoIntegration()]


def register_integration(integration: FrameworkIntegration) -> None:
    """Add a custom integration (checked before the built-in ones)."""
    _INTEGRATIONS.insert(0, integration)


def resolve_integration(app: Any) -> Optional[FrameworkIntegration]:
    """The integration for *app*. Raises ValueError when several match."""
    if app is None:
        return None
    matches = [i for i in _INTEGRATIONS if _safe_match(i, app)]
    names = {i.name for i in matches}
    if len(names) > 1:
        raise ValueError(f"Ambiguous application: matches {sorted(names)}")
    return matches[0] if matches else None


def _safe_match(integration: FrameworkIntegration, app: Any) -> bool:
    try:
        return bool(integration.matches(app))
    except Exception:  # noqa: BLE001 - a broken custom integration must not break setup
        return False
