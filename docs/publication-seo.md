# Search discoverability (`seo.prerender`, `seo.sitemap`)

Published articles only rank if search engines can read them. Client-rendered
sites (React, Vue, Angular single-page apps) serve the same empty shell for
every URL; crawlers that do not run JavaScript see no article, and every page
has the same title and description. The connector fixes this without changes
to the site's frontend:

* **Pre-rendered articles.** `SeoPages.page(slug)` returns the site's own
  `index.html` with the article's title, meta description, canonical URL,
  Open Graph / Twitter tags and `Article` JSON-LD, and the article text in
  the app's root element. Visitors still get the app (it replaces the
  markup when it starts); crawlers get the article.
* **Sitemap.** `SeoPages.sitemap()` lists the site's public pages and every
  published article with its last modification date.

## Mounting

```python
from tekjuice_connector.publication import PublicationReceiver, SeoPages
from tekjuice_connector.adapters.fastapi import create_router, create_seo_router

seo = SeoPages(store, site_url="https://example.com",
               shell_html=load_index_html,                 # the site's index.html
               static_paths=["/", "/features", "/pricing"],
               site_name="Example")
receiver = PublicationReceiver(store=store, website_id=..., receiver_secret=..., seo=seo)
app.include_router(create_router(receiver, path="/api/tekjuice/publish"))
app.include_router(create_seo_router(seo, prefix="/api/tekjuice/seo"))
```

The store needs `get_content(slug)` (and `list_published()` for the sitemap).
Passing `seo=` makes the receiver advertise `seo.prerender` / `seo.sitemap`
to the Data Engine, which then checks after every publication that the live
URL serves the article and that the sitemap lists it.

## Web server routing (nginx example)

Serve `/sitemap.xml` from the connector, and try the pre-rendered page for any
path that is not a static file; anything that is not a published article
(404) falls back to the app's `index.html`:

```nginx
location = /sitemap.xml { proxy_pass http://backend/api/tekjuice/seo/sitemap.xml; }

location / {
    try_files $uri @tekjuice_page;
}
location @tekjuice_page {
    proxy_pass http://backend/api/tekjuice/seo/page$uri;
    proxy_intercept_errors on;
    error_page 404 500 502 503 504 = /index.html;
}
```

If the backend is unavailable the site keeps working: nginx serves the app.
