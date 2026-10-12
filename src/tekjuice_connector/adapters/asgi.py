"""Dependency-free ASGI adapter."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

from tekjuice_connector.publication.receiver import PublicationReceiver

ASGIApp = Callable[[dict, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]


def make_asgi_app(receiver: PublicationReceiver) -> ASGIApp:
    """Return an ASGI application that serves the receiver at its mount point.

    The receiver (and the customer's content store) runs in a worker thread so
    blocking database calls never stall the event loop.
    """

    async def app(scope: Dict[str, Any], receive, send) -> None:
        if scope["type"] != "http":
            return

        if scope.get("method") != "POST":
            await send({"type": "http.response.start", "status": 405,
                        "headers": [(b"allow", b"POST")]})
            await send({"type": "http.response.body", "body": b""})
            return

        limit = receiver.max_body_bytes + 1
        body = b""
        more = True
        while more and len(body) <= limit:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)

        headers = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        response = await asyncio.to_thread(receiver.handle, headers, body)
        payload = response.body_bytes()

        await send({
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [
                *[(k.lower().encode("latin-1"), v.encode("latin-1"))
                  for k, v in response.headers.items()],
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": payload})

    return app
