# The connector sidecar: any language, any framework (0.7.0+)

The sidecar is the Tek Juice connector running as its **own service** next to
your backend, so the backend can be written in any language: Node.js, PHP,
Ruby, Go, Java, .NET, Python or anything else that serves HTTP. The sidecar
owns the whole S2S side; your backend implements four small JSON endpoints
over its own content model.

```
Tek Juice Data Engine ──S2S (Bearer receiver secret)──▶  your TLS reverse proxy
                                                          │  https://api.example.com/tekjuice/publish
                                                          ▼
                                       tekjuice-connector sidecar (:8787)
                                       • receiver secret: generated, stored, rotated
                                       • endpoint URL, registration, verification, readiness
                                       • S2S contract validation, idempotency ledger
                                                          │  private network, Bearer content API token
                                                          ▼
                                       your backend's content API (any language)
                                       GET/POST/PUT /tekjuice/content…
```

It runs the same setup as the in-process connector (see [setup.md](setup.md)):

- it generates the receiver secret on first start;
- it establishes `TEKJUICE_PUBLICATION_ENDPOINT_URL`;
- it proves the public route reaches it with a dry run;
- it registers with the Data Engine and confirms what the Data Engine stored;
- it waits for the Data Engine's own verification;
- it then reports ready on `/readyz`.

It retries in the background until ready. Nothing is published during setup.

## Run it

Docker (recommended):

```bash
docker run -d --name tekjuice-connector --restart unless-stopped \
  -v tekjuice-data:/var/lib/tekjuice \
  -e TEKJUICE_WEBSITE_ID=... \
  -e TEKJUICE_CONNECTOR_KEY_FILE=/run/secrets/tekjuice_connector_key \
  -e TEKJUICE_DATA_ENGINE_URL=https://engine.tekjuice.io \
  -e TEKJUICE_API_BASE_URL=https://api.example.com \
  -e TEKJUICE_CONTENT_API_URL=http://backend:3000/tekjuice \
  -e TEKJUICE_CONTENT_API_TOKEN_FILE=/run/secrets/tekjuice_content_api_token \
  tekjuice-connector:0.8.0
```

Get the image by building it straight from the public repository (no
registry login, any machine with Docker; pin the release tag):

```bash
docker build -t tekjuice-connector:0.8.0 \
  "https://github.com/tek-juice/Tek-Juice-Data-Engine-Connector.git#v0.8.0"
```

and use `tekjuice-connector:0.8.0` as the image name above. Releases are
also pushed to `ghcr.io/tek-juice/tekjuice-connector:<version>` (linux/amd64
and linux/arm64); pulling from there works without a login only once the
package is public.

A complete Compose file is in `examples/sidecar/docker-compose.yml`. Without
Docker, run `pipx install tekjuice-connector` and then `tekjuice serve`
under systemd. It needs only Python on the host, not in your application.

Then expose the route through your reverse proxy, e.g. nginx:

```nginx
location = /tekjuice/publish {
    proxy_pass http://127.0.0.1:8787;
    proxy_set_header Authorization $http_authorization;   # must reach the sidecar
    client_max_body_size 3m;
}
```

## Configuration (environment)

| Variable | Required | Meaning |
|---|---|---|
| `TEKJUICE_WEBSITE_ID` | yes | The website in Tek Juice |
| `TEKJUICE_CONNECTOR_KEY` / `_FILE` | yes | Onboarding credential (issued in Tek Juice) |
| `TEKJUICE_DATA_ENGINE_URL` | yes | Data Engine API URL |
| `TEKJUICE_API_BASE_URL` | yes* | Public https base URL where your proxy exposes the sidecar's route. Can differ from the website's domain. |
| `TEKJUICE_ROUTE_PATH` | no | Receiver route, default `/tekjuice/publish` |
| `TEKJUICE_CONTENT_API_URL` | yes | Your backend's content API, e.g. `http://backend:3000/tekjuice` |
| `TEKJUICE_CONTENT_API_TOKEN` / `_FILE` | yes, unless the API is on a private network | Shared by the sidecar and your backend |
| `TEKJUICE_PUBLIC_URL_TEMPLATE` | if `/capabilities` does not return it | e.g. `https://www.example.com/blog/{slug}` |
| `TEKJUICE_PUBLIC_CONTENT_URL` | client-rendered sites | Template of an anonymous URL serving published content (see the contract) |
| `TEKJUICE_DATA_DIR` | no | `/var/lib/tekjuice`: receiver secret and ledger. **Use a named volume.** |
| `TEKJUICE_LISTEN` | no | `0.0.0.0:8787` |
| `TEKJUICE_ALLOW_ENDPOINT_CHANGE` | no | `1` to move an already verified registration to a new endpoint |

`_FILE` variants read Docker or Kubernetes secret files.

*Without it, setup stops with `api_host_not_discoverable`. It never guesses
the host from the website's domain.

## Three credentials, three roles

| Credential | From → to | Who creates it |
|---|---|---|
| Connector key | sidecar → Data Engine (onboarding handshake) | Tek Juice issues it |
| Receiver secret | Data Engine → sidecar (every S2S publication) | **the sidecar generates it** (`secrets.token_urlsafe(48)`), stores it in its volume and registers it; never printed |
| Content API token | sidecar → your backend | you: any long random value in both services' secrets |

None is reused for another role. Your backend never sees the receiver secret
or the connector key.

## The content API your backend implements

Full contract: [content-api.openapi.yaml](content-api.openapi.yaml). You can
generate a server stub from it in your language. Paths are relative to
`TEKJUICE_CONTENT_API_URL`:

| Request | Answer |
|---|---|
| `GET /capabilities` (optional) | `{"update": true, "validate": true, "public_url_template": "https://…/{slug}"}` |
| `GET /content/{slug}` | `200 {"slug","url","id","idempotency_key"}` or `404` |
| `POST /content` | `201 {"slug","url","id"}`; `409` if the slug belongs to other content; `422 {"errors":[…]}` if your rules reject it |
| `PUT /content/{slug}` | `200 {…}`; `404`; `422` |
| `POST /content/validate` (optional) | dry run: `200` or `422`; must write nothing |

Bodies contain `slug, title, body (HTML), excerpt, meta_description,
meta_keywords, discovery, execution_id, idempotency_key, content_hash`.
Answer `5xx` or `429` for temporary failures, and the Data Engine retries.
The sidecar keeps the idempotency ledger, so a redelivered publication never
reaches your API twice. Still, store `idempotency_key` with the content: it
lets an interrupted create be recognised instead of conflicting.

Reference implementations. Each one is run end to end against the sidecar by
`tests/integration/test_sidecar_languages.py`, covering setup, publish,
redelivery, update, slug conflict and a wrong token:

| Language / framework | Where |
|---|---|
| Node.js (no dependencies) | `examples/sidecar/node/content-api.js` |
| PHP 8.3 (no dependencies) | `examples/sidecar/php/content-api.php` |
| Go 1.22 (`net/http`) | `examples/sidecar/go/main.go` |
| Express | `examples/sidecar/frameworks/express/` |
| Laravel 11 | `examples/sidecar/frameworks/laravel/` |
| Ruby on Rails 7.2 | `examples/sidecar/frameworks/rails/` |
| Spring Boot 3 | `examples/sidecar/frameworks/spring-boot/` |
| ASP.NET Core 8 | `examples/sidecar/frameworks/aspnet/` |

The framework examples build as Docker images
(`examples/sidecar/frameworks/build-images.sh`). Any other framework works the
same way: it serves the HTTP contract, which `tekjuice check-content-api`
checks.

### Check your implementation

```bash
docker exec tekjuice-connector tekjuice check-content-api
```

It runs non-mutating checks with the sidecar's own configuration:

- capabilities and the public URL template;
- unauthenticated requests are rejected;
- an unknown slug returns 404;
- validate accepts and rejects correctly.

## Health, readiness, diagnostics

- `GET /healthz`: the process is alive.
- `GET /readyz`: `200` once the Data Engine reports the website ready.
  Otherwise `503`, with the secret-free stage report (code, failing stage,
  next action). Use it as your orchestrator's readiness probe.
- Setup codes are listed in [setup.md](setup.md#stages-and-diagnostic-codes).

Setup stops retrying on configuration errors, which a restart with corrected
configuration fixes: an invalid connector key, website mismatch, unknown API
host, no persistent storage, or an unconfirmed endpoint change. It keeps
retrying, with backoff up to 10 minutes, while the Data Engine or your
backend is unreachable.

## Rotation and recovery

```bash
docker exec tekjuice-connector tekjuice setup \
  --secret-file /var/lib/tekjuice/receiver-secret.json --route-path /tekjuice/publish --rotate
```

1. A pending secret is generated next to the active one. The running sidecar
   accepts both, because it reloads the secret file when a credential does not
   match.
2. The pending secret is registered and the Data Engine verifies it.
3. Only then does it become the active secret.

A failed rotation keeps both secrets accepted, and retrying reuses the pending
one. `--rollback-rotation` abandons it.

The volume holds the receiver secret and the ledger, so back it up with your
other secrets. If it is lost, the sidecar generates a new secret and
registers it, and the Data Engine verifies it again. The old secret stops
working.

## HTTPS

Choose one:

- **Your reverse proxy terminates TLS.** This is the usual setup. Generate its
  configuration as described below.
- **The sidecar serves HTTPS itself.** Set `TEKJUICE_TLS_CERT_FILE` and
  `TEKJUICE_TLS_KEY_FILE` (PEM; TLS 1.2+). Renewed certificates (certbot,
  cert-manager, ...) are reloaded within a minute without a restart.
- **Caddy in front, with automatic certificates** from Let's Encrypt:
  `examples/sidecar/docker-compose.yml` includes it, using the Caddyfile that
  `tekjuice proxy-config --caddy` generates.

## Proxy configuration, generated

```bash
docker exec tekjuice-connector tekjuice proxy-config --caddy \
    --upstream tekjuice-connector:8787 --site-upstream backend:3000 > Caddyfile
docker exec tekjuice-connector tekjuice proxy-config --nginx \
    --upstream http://127.0.0.1:8787 --site-upstream http://127.0.0.1:3000 > tekjuice.conf
```

The configuration is built from the sidecar's own settings:

- **On the API host:** the receiver route, with `Authorization` passed through.
- **On the website host** (when discoverability is on):
  - the sitemap, robots.txt, llms.txt, llms-full.txt and feed;
  - the IndexNow key file;
  - the article pages and their `.md` copies (from the public URL template;
    pass `--template` if your content API declares it instead);
  - your static pages.

Each website route asks the sidecar first and **falls back to your site when
the sidecar answers 404**, so nothing your site serves is hidden. The sidecar
does the path mapping itself (`/tekjuice/seo/site/<path>`), so the generated
configurations contain no fragile rewrites. Both outputs are tested against
real nginx and Caddy (`tests/integration/test_sidecar_proxy_tls.py`).

Review the output, then install it. The connector never edits a running proxy.

## Discoverability and site changes (any language)

Set `TEKJUICE_SITE_URL` to turn on, for any backend:

- **Pre-rendered article pages** for crawlers. Your HTML shell comes from
  `TEKJUICE_SHELL_URL`, by default the site's homepage, and is cached for
  5 minutes.
- **Machine-readable outputs:** sitemap, robots.txt, llms.txt, llms-full.txt,
  JSON feed, Markdown copies, the business's verified facts (`entity.json`)
  and the IndexNow key.
- **Site changes the Data Engine sends after approval:** metadata overrides
  (title, description, canonical, robots) on your own pages listed in
  `TEKJUICE_STATIC_PATHS`, redirects, and sitemap inclusion or exclusion.
  Backends that render pages server-side can also read
  `/tekjuice/seo/site-overrides.json` and apply them themselves.

The sidecar keeps a copy of what it published, which is public content, to
serve these. It advertises the `seo.*`, `ai.*` and `site.changes*`
capabilities to the Data Engine only when this is on.

| Variable | Meaning |
|---|---|
| `TEKJUICE_SITE_URL` | Public website URL (turns discoverability on) |
| `TEKJUICE_SHELL_URL` | Where to fetch the site's HTML shell (default: the homepage) |
| `TEKJUICE_STATIC_PATHS` | Your own pages that may receive site changes, e.g. `/,/pricing,/features` |
| `TEKJUICE_PRIVATE_PATHS` | Paths robots.txt disallows, e.g. `/account,/admin` |
| `TEKJUICE_SITE_NAME`, `TEKJUICE_SITE_DESCRIPTION` | Used in llms.txt, feed and structured data |
| `TEKJUICE_HUB_PATH`, `TEKJUICE_HUB_TITLE`, `TEKJUICE_HUB_DESCRIPTION` | Server-rendered article index (e.g. `/blog`) |
| `TEKJUICE_SEO_PREFIX` | Default `/tekjuice/seo` |
