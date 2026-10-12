"""Dependency-free WSGI adapter."""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Tuple

from tekjuice_connector.publication.receiver import PublicationReceiver

_REASONS = {
    200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 409: "Conflict",
    413: "Payload Too Large", 422: "Unprocessable Entity",
    500: "Internal Server Error", 503: "Service Unavailable",
}


def _headers_from_environ(environ: dict) -> Dict[str, str]:
    headers = {
        key[5:].replace("_", "-").title(): value
        for key, value in environ.items()
        if key.startswith("HTTP_")
    }
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = environ["CONTENT_TYPE"]
    return headers


def make_wsgi_app(receiver: PublicationReceiver) -> Callable:
    """Return a WSGI application that serves the receiver at its mount point."""

    def app(environ: dict, start_response: Callable) -> Iterable[bytes]:
        if environ.get("REQUEST_METHOD") != "POST":
            start_response("405 Method Not Allowed", [("Allow", "POST")])
            return [b""]

        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            length = 0
        # Read at most one byte past the limit so oversize bodies are detected
        # without buffering an unbounded request.
        limit = receiver.max_body_bytes + 1
        body = environ["wsgi.input"].read(min(length, limit)) if length else b""

        response = receiver.handle(_headers_from_environ(environ), body)
        payload = response.body_bytes()
        headers: List[Tuple[str, str]] = list(response.headers.items())
        headers.append(("Content-Length", str(len(payload))))
        status = f"{response.status_code} {_REASONS.get(response.status_code, '')}".strip()
        start_response(status, headers)
        return [payload]

    return app
