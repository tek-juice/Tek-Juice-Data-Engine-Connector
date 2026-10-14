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
    """Return an ``APIRouter`` serving the site's discoverability outputs:

    * ``{prefix}/sitemap.xml`` and ``{prefix}/robots.txt``
    * ``{prefix}/page/{slug}``: the pre-rendered article page
    * ``{prefix}/markdown/{slug}``: the article as Markdown (serve it at
      ``/{slug}.md``)
    * ``{prefix}/llms.txt``, ``{prefix}/llms-full.txt``, ``{prefix}/feed.json``
    * ``{prefix}/indexnow/{key}``: the IndexNow key file (serve it at
      ``/{key}.txt``)

    Article routes return 404 for anything that is not a published article,
    so the web server can fall back to the site's normal page. Article
    responses carry a ``Link: <canonical>; rel="canonical"`` header.
    """
    try:
        from fastapi import APIRouter
        from fastapi.responses import Response
        from starlette.concurrency import run_in_threadpool
    except ImportError as exc:  # pragma: no cover
        raise ImportError("tekjuice_connector.adapters.fastapi requires FastAPI.") from exc

    router = APIRouter()

    def site_wide(method, media_type, max_age=900):
        async def handler() -> Response:
            body = await run_in_threadpool(method)
            return Response(content=body, media_type=media_type,
                            headers={"Cache-Control": f"public, max-age={max_age}"})
        return handler

    def article(method, media_type):
        async def handler(slug: str) -> Response:
            body = await run_in_threadpool(method, slug)
            if body is None:
                return Response(status_code=404)
            canonical = seo.canonical_url(slug)
            return Response(content=body, media_type=media_type, headers={
                "Cache-Control": "public, max-age=300",
                "Link": f'<{canonical}>; rel="canonical"',
            })
        return handler

    routes = [
        ("sitemap.xml", site_wide(seo.sitemap, "application/xml"), "sitemap"),
        ("robots.txt", site_wide(seo.robots_txt, "text/plain; charset=utf-8"), "robots"),
        ("llms.txt", site_wide(seo.llms_txt, "text/plain; charset=utf-8"), "llms"),
        ("llms-full.txt", site_wide(seo.llms_full_txt, "text/plain; charset=utf-8"), "llms_full"),
        ("feed.json", site_wide(seo.feed_json, "application/feed+json"), "feed"),
        ("page/{slug}", article(seo.page, "text/html; charset=utf-8"), "page"),
        ("markdown/{slug}", article(seo.markdown, "text/markdown; charset=utf-8"), "markdown"),
    ]

    async def indexnow(key: str) -> Response:
        body = seo.indexnow_key_file(key)
        if body is None:
            return Response(status_code=404)
        return Response(content=body, media_type="text/plain; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=86400"})

    routes.append(("indexnow/{key}", indexnow, "indexnow"))
    for path, handler, name in routes:
        router.add_api_route(f"{prefix}/{path}", handler, methods=["GET"],
                             include_in_schema=False, name=f"tekjuice_seo_{name}")
    return router
