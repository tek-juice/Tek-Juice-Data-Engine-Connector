"""FastAPI / Starlette adapter.

Usage::

    from tekjuice_connector.adapters.fastapi import create_router
    app.include_router(create_router(receiver, path="/tekjuice/publish"))
"""

# No ``from __future__ import annotations`` here: FastAPI resolves the
# ``Request`` annotation of the route at runtime, and it is imported lazily.

from tekjuice_connector.publication.receiver import PublicationReceiver


def create_router(receiver: "PublicationReceiver", *, path: str = "/tekjuice/publish"):
    """Return an ``APIRouter`` with a single POST route serving the receiver."""
    try:
        from fastapi import APIRouter, Request
        from fastapi.responses import Response
        from starlette.concurrency import run_in_threadpool
    except ImportError as exc:  # pragma: no cover - exercised via message test
        raise ImportError(
            "tekjuice_connector.adapters.fastapi requires FastAPI. Install it, "
            "or use tekjuice_connector.adapters.asgi.make_asgi_app instead."
        ) from exc

    router = APIRouter()

    async def receive_publication(request: Request) -> Response:
        body = await request.body()
        result = await run_in_threadpool(receiver.handle, dict(request.headers), body)
        return Response(
            content=result.body_bytes(),
            status_code=result.status_code,
            media_type="application/json",
        )

    router.add_api_route(
        path,
        receive_publication,
        methods=["POST"],
        include_in_schema=False,
        name="tekjuice_receive_publication",
    )
    return router
