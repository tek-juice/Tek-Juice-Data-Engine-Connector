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


def create_seo_router(seo, *, prefix: str = "/tekjuice/seo"):
    """Return an ``APIRouter`` serving ``{prefix}/sitemap.xml`` and
    pre-rendered article pages at ``{prefix}/page/{slug}`` (404 for anything
    that is not a published article, so the web server can fall back to the
    site's normal page)."""
    try:
        from fastapi import APIRouter
        from fastapi.responses import Response
        from starlette.concurrency import run_in_threadpool
    except ImportError as exc:  # pragma: no cover
        raise ImportError("tekjuice_connector.adapters.fastapi requires FastAPI.") from exc

    router = APIRouter()

    async def sitemap() -> Response:
        body = await run_in_threadpool(seo.sitemap)
        return Response(content=body, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=900"})

    async def page(slug: str) -> Response:
        body = await run_in_threadpool(seo.page, slug)
        if body is None:
            return Response(status_code=404)
        return Response(content=body, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300"})

    router.add_api_route(f"{prefix}/sitemap.xml", sitemap, methods=["GET"],
                         include_in_schema=False, name="tekjuice_seo_sitemap")
    router.add_api_route(f"{prefix}/page/{{slug}}", page, methods=["GET"],
                         include_in_schema=False, name="tekjuice_seo_page")
    return router
