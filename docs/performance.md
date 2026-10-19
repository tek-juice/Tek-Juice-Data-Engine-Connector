# Performance controls (`performance.*`)

The Tek Juice Data Engine measures your site with Google PageSpeed Insights.
Some findings are delivery settings rather than code. With the performance
middleware installed, the Data Engine can change those settings through your
existing S2S receiver, after your approval, without editing your code,
templates, HTML or content.

## Install

```python
from tekjuice_connector.publication import PublicationReceiver
from tekjuice_connector.publication.performance import PerformanceControls

controls = PerformanceControls("/var/lib/myapp/tekjuice-performance.json")  # persisted, 0600
app = controls.asgi(app)            # FastAPI / Starlette / Django ASGI
# app.wsgi_app = controls.wsgi(app.wsgi_app)   # Flask / Django WSGI

receiver = PublicationReceiver(store=store, website_id=..., receiver_secret=..., seo=seo,
                               performance=controls)
```

Nothing changes until the Data Engine sends an approved change. Without the
file path the configuration lives in memory (single process, tests).

## What is implemented, and therefore advertised

| Capability | Version | What it does |
|---|---|---|
| `performance.static_cache` | 1 | `Cache-Control: public, max-age=N, immutable` on GET/HEAD 200 responses for **content-hashed** static files under the approved directories |
| `performance.compression` | 1 | gzip (Brotli when the `brotli` package is installed) for compressible 200 responses the client accepts |
| `performance.snapshot` | 1 | every change returns the previous configuration; `restore` puts it back |

Capabilities are advertised only once the middleware wraps the application.
`performance.image_delivery` and `performance.cdn` are **not implemented**:
requests for them get a structured `unsupported_capability` error.

### Safety rules (enforced by the connector, not only by the Data Engine)

Static caching never applies to: HTML, JSON/API responses, responses that
set cookies, requests with `Authorization`, responses the application marked
`private` or `no-store`, unversioned file names, or `/`. Directories must be
explicit (`/assets/`). Compression never re-encodes, never touches partial
(`Content-Range`), `no-transform` or streaming (`text/event-stream`)
responses, buffers at most 8 MiB, adds `Vary: Accept-Encoding` and weakens
strong ETags.

Responses that never pass through your application (files served directly
by nginx or a CDN in front of it) are out of reach. The middleware records
which static directories it has served and reports them in discovery, so
the Data Engine does not propose changes it cannot make.

## S2S actions (contract 1.0)

Authenticated exactly like publications (`Authorization: Bearer <receiver secret>`).

Capability discovery (writes nothing):

```json
{"contract_version": "1.0", "action": "capabilities", "website_id": "..."}
```

```json
{"status": "ok", "discovery_version": "1", "contract_versions": ["1.0"], "connector_version": "...",
 "capabilities": ["performance.compression", "performance.snapshot", "performance.static_cache", "publish.create", "..."],
 "capability_details": {"performance.static_cache": {"version": "1", "observed_static_prefixes": ["/assets/"], "...": "..."}},
 "unsupported": {"performance.cdn": "no CDN integration is configured for this connector"}}
```

Changes (idempotent per `idempotency_key`; a repeat replays the first result):

```json
{"action": "performance_change", "idempotency_key": "...",
 "change": {"type": "static_cache", "enabled": true, "max_age": 31536000, "path_prefixes": ["/assets/"]}}
{"change": {"type": "compression", "enabled": true, "min_bytes": 1024}}
{"change": {"type": "restore", "snapshot": {"static_cache": null, "compression": null}}}
```

Response: `{"status": "applied", "replayed": false, "previous": {...}, "current": {...}, "config_version": 3}`.

The sidecar does not offer these capabilities: it runs beside your backend,
not around it.
