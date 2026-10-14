# Changelog

All notable changes to the Tek Juice Data Engine Connector are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
