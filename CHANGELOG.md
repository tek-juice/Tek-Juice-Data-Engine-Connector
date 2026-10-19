# Changelog

All notable changes to the Tek Juice Data Engine Connector are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added
- Performance controls (`publication.performance`, docs/performance.md):
  `PerformanceControls` with ASGI and WSGI middleware applying approved,
  reversible delivery settings: `performance.static_cache` (long-lived
  `Cache-Control` for content-hashed static files under approved
  directories only; never HTML, JSON/API, Set-Cookie, credentialed or
  private/no-store responses) and `performance.compression` (gzip, Brotli
  when installed; never streaming, partial or already-encoded responses),
  with `performance.snapshot` (previous configuration returned, `restore`).
  Advertised only once the middleware wraps the application.
- S2S `capabilities` action: authenticated, versioned capability discovery
  (`discovery_version`, `capability_details`, observed static directories,
  `unsupported` with reasons). Writes nothing.
- S2S `performance_change` action (idempotent per key). Image and CDN
  changes are refused with the structured `unsupported_capability` error
  (`error.capability`), as nothing implements them.
- `PublicationReceiver(performance=...)`; `capabilities` is now computed
  (the static set plus installed performance controls).

## [0.7.1] — 2026-10-19

### Added
- Pre-rendered article pages embed the article as JSON
  (`<script type="application/json" id="tekjuice-content">`: slug, title,
  excerpt, body, the escaped body HTML, meta description, keywords, canonical URL,
  modified time) and
  the article index embeds its list (`id="tekjuice-hub"`). A single-page app
  that reads it before its first render shows the same content on its first
  frame: no API round trip, no loading state, so replacing the pre-rendered
  HTML neither shifts the layout (CLS) nor delays the largest paint (LCP).
  The JSON is escaped so content cannot end the script element.

## [0.7.0] — 2026-10-19

### Added
- `TekJuiceConnector.setup()` and `tekjuice setup`: connector-owned S2S setup.
  It generates the receiver secret on the customer server
  (`secrets.token_urlsafe(48)`, only when none is stored), persists it,
  establishes `TEKJUICE_PUBLICATION_ENDPOINT_URL` from the configured API base
  URL and the receiver route (mounting the route on FastAPI/Starlette/Flask;
  finding it in Django's URLconf), proves the public endpoint accepts the
  secret with a dry-run `verify`, registers through the existing handshake,
  confirms the Data Engine stored the same endpoint and secret fingerprint,
  waits for the Data Engine's verification and reports readiness per stage
  (`SetupResult`, `SetupCode`). Publishes nothing; idempotent.
- Secret stores: `EnvFileSecretStore` (atomic, 0600, refuses git-tracked or
  un-ignored files), `FileSecretStore`, read-only `EnvironmentSecretStore`.
  Inside containers, writable stores refuse the container's own filesystem.
  Custom stores implement `SecretStore`.
- Coordinated rotation (`rotate=True`): a pending secret is accepted beside the
  active one and replaces it only after the Data Engine verifies it; retries
  reuse it; `rollback_rotation=True` abandons it.
- `PublicationReceiver(secret_store=...)`: accepts the stored active (and
  pending) secret and reloads the store when a credential does not match, so
  every worker follows the setup and rotations; `set_receiver_secrets()`.
- Optional handshake response fields `publication_endpoint_url`,
  `receiver_secret_fingerprint`, `publication_failure_reason` (Data Engine
  connector-setup release), exposed on `ConnectionResult`.
- Diagnostics include the last setup's stages.
- **Sidecar for any language** (`tekjuice serve`, `Dockerfile`): the connector as
  its own service. It is the S2S receiver, runs the setup in the background,
  serves `/healthz` and `/readyz`, keeps the idempotency ledger, and hands
  content to the backend's content API (`HttpContentStore`;
  `docs/content-api.openapi.yaml`). Reference content APIs in Node.js, PHP and
  Go (`examples/sidecar/`), tested end to end; `tekjuice check-content-api`
  runs non-mutating conformance checks.
- Sidecar HTTPS (`TEKJUICE_TLS_CERT_FILE` / `TEKJUICE_TLS_KEY_FILE`, renewed
  certificates reloaded without a restart) and `tekjuice proxy-config
  --nginx|--caddy`, generating proxy configuration from the sidecar's settings.
  Website routes fall back to the site on 404. Both outputs are tested against
  real nginx and Caddy.
- Discoverability and site changes for any language (`TEKJUICE_SITE_URL`):
  pre-rendered articles, sitemap, robots.txt, llms.txt, feed, Markdown,
  entity, IndexNow key, metadata overrides and redirects, served by the
  sidecar from its copy of the published content (`/tekjuice/seo/site/<path>`
  maps website paths).
- Framework reference content APIs, tested end to end: Express, Laravel, Ruby
  on Rails, Spring Boot, ASP.NET Core (`examples/sidecar/frameworks/`).

### Compatibility
- `PublicationReceiver(receiver_secret=...)`, `PublicationRegistration`,
  `connect()`, `status()`, `diagnostics()` and the handshake contract are
  unchanged; older Data Engines without the new response fields still work.

## [0.6.0] — 2026-10-15

### Added
- Sitemap inclusion/exclusion site changes (`site.changes.sitemap`): approved
  overrides in the content store, applied to the sitemap `SeoPages` serves.

## [0.5.2] — 2026-10-15

### Added
- `SeoPages(hub_path=..., hub_title=..., hub_description=...)`: the site's
  article index (e.g. `/resources`) served server-rendered with its own
  canonical, a `CollectionPage`/`ItemList` and a plain link to every
  published article, so crawlers that do not run JavaScript can reach them.
  Off unless configured.

## [0.5.1] — 2026-10-14

### Fixed
- The FastAPI SEO routes (pre-rendered pages, sitemap, robots.txt, llms.txt,
  feed, Markdown copies, entity.json, IndexNow key) answer `HEAD` as well as
  `GET`. They returned 405 to `HEAD`, which crawlers and SEO tools report as
  an error.

## [0.5.0] — 2026-10-14

### Added
- AI discoverability (GEO / AEO / LEO / voice). The Data Engine may send
  `content.discovery` (summary, key takeaways, entities, direct answer, FAQ,
  citations, JSON-LD, language) to receivers advertising `publish.discovery`
  (always advertised from 0.5.0). It is validated, size-limited
  (`DISCOVERY_MAX_BYTES`), stored, and excluded from the content hash.
- Pre-rendered pages render it: Article JSON-LD (URL forced to the canonical
  URL, publisher, `dateModified`, `about`, `citation`, `speakable`),
  FAQPage and BreadcrumbList JSON-LD; visible direct answer, key takeaways
  and FAQ; `<link rel="alternate" type="text/markdown">`. JSON-LD from the
  site's shell is replaced.
- `SeoPages.markdown`, `llms_txt`, `llms_full_txt`, `feed_json` (JSON Feed
  1.1), `robots_txt` (welcomes AI crawlers, keeps `private_paths` closed);
  every output names the article's canonical URL. `create_seo_router` serves
  them; article responses carry a `Link: rel="canonical"` header.
- With `seo=`, the receiver also advertises `ai.markdown`, `ai.llms_txt`,
  `ai.feed`, `ai.robots` and `ai.indexnow`: `create_seo_router` serves the
  site's IndexNow key (`indexnow_key`, derived from the receiver secret) at
  `{prefix}/indexnow/{key}`; route `/<key>.txt` there so the Data Engine can
  notify Bing, Yandex and other IndexNow engines when an article goes live.
- `SQLiteContentStore` stores `discovery` (column added automatically).
- Verified business facts: `content.discovery.entity` (name, website,
  description, category, product type, products, features, use cases,
  audiences, locations, pricing as stated on the site). The receiver
  advertises `ai.entity`; pages carry Organization and Product JSON-LD (the
  organization URL is always this site), `llms.txt` gains an "About"
  section, and `create_seo_router` serves `entity.json`.
- Structured data follows the page: software products are
  SoftwareApplication (with `applicationCategory`, `operatingSystem` and
  `featureList` from verified facts); an Offer / AggregateOffer is added
  only when the verified pricing states a price and currency; on article
  pages the product appears only when the page shows its name, and its
  offer only when the page shows the price.
- Site changes (`site.changes`, action `site_change`): approved fixes to the
  customer's own pages that the Data Engine's audits found: head metadata
  overrides (title, description, canonical, robots) applied by `SeoPages`
  to the site's own page, and redirects (301/302/307/308) answered by
  `create_seo_router`, for example to consolidate overlapping articles.
  Same-site targets only, no loops or chains, idempotent replay, and each
  response carries the previous state for rollback. Advertised only when
  the store implements `get/set/delete/list_site_override(s)` and `seo=`
  is mounted. `create_seo_router` serves `page/` (home) and nested paths.

## [0.4.0] — 2026-10-14

### Added
- Search discoverability: `SeoPages` pre-renders published articles (own
  title, meta description, canonical URL, Open Graph/Twitter tags, Article
  JSON-LD, article text in the app root) and builds `sitemap.xml` from the
  content store. `create_seo_router` mounts both on FastAPI.
- `PublicationReceiver(seo=...)` advertises `seo.prerender` and
  `seo.sitemap`; the Data Engine verifies them after each publication.
- `SQLiteContentStore` / `InMemoryContentStore`: `get_content` and
  `list_published`.

## [0.3.0] — 2026-10-12

### Added

- `PublicationRegistration(public_content_url=...)`: client-rendered sites can declare an
  anonymous HTTPS URL template (with `{slug}`) serving the published content their pages
  render, so the Data Engine can prove publication on sites whose HTML is an empty shell.

## [0.2.0] — 2026-10-12

### Added

- S2S publication receiver (`tekjuice_connector.publication`): versioned contract v1.0
  (`create`, `update`, non-persisting `verify`), bearer authentication with constant-time
  comparison, strict payload validation, content-hash check, receiver-side idempotency
  (`idempotency_key` replay), duplicate-slug protection, structured errors whose HTTP
  status tells the Data Engine whether to retry.
- `ContentStore` protocol so customers persist content in their own model; capabilities
  (`publish.create`, `publish.update`, `publish.verify`) are derived from what the store
  implements. Reference `InMemoryContentStore` and `SQLiteContentStore`.
- Framework adapters: dependency-free WSGI and ASGI, plus FastAPI, Flask and Django
  wrappers that import their framework lazily.
- `PublicationRegistration` and `TekJuiceConnector(publication=...)`: the handshake now
  registers the receiver endpoint, secret, contract versions and capabilities.
- `ConnectionResult.publication_status`, `publication_ready`, `next_action`.
- Docs: `docs/publication.md`, `docs/publication-contract.md`.

### Changed

- 4xx handshake rejections other than 401/403 raise `ApiError` with the Data Engine's
  message and are no longer retried.
- Project URLs point to `tek-juice/Tek-Juice-Data-Engine-Connector`.

## [0.1.0] — 2026-10-07

### Added

- `TekJuiceConnector` public class with `connect()`, `status()`, and `diagnostics()`.
- Configuration loading from environment variables (`TEKJUICE_WEBSITE_ID`, `TEKJUICE_CONNECTOR_KEY`, `TEKJUICE_DATA_ENGINE_URL`).
- Explicit Python configuration support — keyword arguments override environment variables.
- Optional `.env` file loading via `python-dotenv` (not required).
- E2E handshake against `POST /api/v1/connector/handshake` with full response validation.
- Typed models: `ConnectorCredentials`, `HandshakeRequest`, `HandshakeResponse`, `ConnectionResult`, `ConnectionState`, `AuthSession`.
- Full exception hierarchy: `TekJuiceConnectorError` and all subclasses.
- Secret redaction: connector key never appears in `repr()`, logs, exceptions, or diagnostic output.
- `SecretStr` wrapper for safe secret handling.
- HTTPS enforcement with `allow_http` development override.
- Bounded exponential backoff with full jitter (`RetryPolicy`, configurable `max_attempts` capped at 10).
- Permanent vs transient failure distinction — auth failures are never retried.
- `SanitizingFilter` for Python `logging` — auto-attached to the package logger.
- `Diagnostician` / `DiagnosticReport` with configuration and connectivity checks.
- CLI: `tekjuice status`, `tekjuice test-connection`, `tekjuice diagnostics`, `tekjuice --version`.
- Framework-neutral integration layer: `ConnectorHooks`, `ConnectorContext`, `ConnectorAdapter` protocol, `connect_on_startup`, `disconnect_on_shutdown`.
- Connection health check via `check_health()` (non-throwing).
- 188 automated tests across unit, integration, and contract suites.
- Full documentation in `docs/`.
- Examples in `examples/`.

### Security

- Connector key never included in any log output, exception message, repr(), or diagnostic report.
- `SanitizingFilter` scrubs long token-like values from all log records.
- URL security validation: no embedded credentials, HTTPS required in production.

### Notes

- This release has not been tested against a production Tek Juice Data Engine.
  Tests use a mocked HTTP transport.
- M2M authentication is explicitly out of scope and not implemented.
