# Search and AI discoverability (`seo.*`, `ai.*`, `publish.discovery`)

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

## AI answer engines (GEO, AEO, LEO, voice)

The goal is that AI assistants and answer engines quote the site's articles
and link the article's canonical URL on the customer's own domain. The
Data Engine sends `content.discovery` with each publication (only to
receivers advertising `publish.discovery`): summary, key takeaways,
entities, a direct answer, FAQ, citations and JSON-LD. The connector uses it:

* **GEO / AEO** – pre-rendered pages carry Article, FAQPage and
  BreadcrumbList JSON-LD whose URLs are always the canonical URL (URLs in the
  supplied JSON-LD are ignored), the publisher, `dateModified`, entities as
  `about` and sources as `citation`; the direct answer, key takeaways and FAQ
  are visible text.
* **Voice** – `speakable` points assistants at the direct answer
  (`#tj-answer`) and summary (`#tj-summary`).
* **LEO** – `SeoPages.markdown(slug)` (front matter with `canonical:` and a
  `Source:` line), `llms_txt()`, `llms_full_txt()`, `feed_json()` (JSON
  Feed 1.1) and `robots_txt()`, which allows search and AI crawlers (GPTBot,
  ClaudeBot, PerplexityBot, Google-Extended, ...) on public content and keeps
  `private_paths` closed.

* **IndexNow** – the receiver derives the site's IndexNow key from its
  secret and `create_seo_router` serves it at `indexnow/{key}`. Route
  `/<key>.txt` there (see the nginx example) and the Data Engine notifies
  Bing (which feeds ChatGPT search and Copilot), Yandex and the other
  IndexNow engines as soon as each article is live.

`create_seo_router` serves all of them under its prefix: `sitemap.xml`,
`robots.txt`, `llms.txt`, `llms-full.txt`, `feed.json`, `page/{slug}`,
`markdown/{slug}`. Article responses carry `Link: <canonical>; rel="canonical"`.

## Mounting

```python
from tekjuice_connector.publication import PublicationReceiver, SeoPages
from tekjuice_connector.adapters.fastapi import create_router, create_seo_router

seo = SeoPages(store, site_url="https://example.com",
               shell_html=load_index_html,                 # the site's index.html
               static_paths=["/", "/features", "/pricing"],
               site_name="Example",
               site_description="What the product is, in one sentence.",
               private_paths=["/dashboard", "/api/"])
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
location = /sitemap.xml    { proxy_pass http://backend/api/tekjuice/seo/sitemap.xml; }
location = /robots.txt     { proxy_pass http://backend/api/tekjuice/seo/robots.txt; }
location = /llms.txt       { proxy_pass http://backend/api/tekjuice/seo/llms.txt; }
location = /llms-full.txt  { proxy_pass http://backend/api/tekjuice/seo/llms-full.txt; }
location = /feed.json      { proxy_pass http://backend/api/tekjuice/seo/feed.json; }
location ~ ^/([A-Za-z0-9_-]+)\.md$ { proxy_pass http://backend/api/tekjuice/seo/markdown/$1; }
location ~ "^/([a-f0-9]{32})\.txt$" { proxy_pass http://backend/api/tekjuice/seo/indexnow/$1; }

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
