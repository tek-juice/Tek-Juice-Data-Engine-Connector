# Connector setup: S2S publication end to end (0.7.0+)

> Backend not written in Python? Run the connector as a sidecar instead: it
> runs this same setup for a backend in any language. See [sidecar.md](sidecar.md).

`TekJuiceConnector.setup()` (or `tekjuice setup`) does the integration work
for S2S (server-to-server) publication. It does not print instructions for
you to follow. In one idempotent call it:

1. authenticates the connector with the Data Engine (the onboarding handshake);
2. identifies the framework and finds the receiver route, or mounts it;
3. generates the **receiver secret** on your server, but only when none is
   stored, and persists it;
4. establishes `TEKJUICE_PUBLICATION_ENDPOINT_URL` from your configured API host
   and the receiver's route;
5. proves through the public URL that the endpoint reaches *this* receiver and
   that the receiver accepts the secret, using a dry run;
6. registers the endpoint and secret with the Data Engine, and confirms that the
   Data Engine stored the same endpoint and the same secret;
7. waits for the Data Engine's own dry-run verification from its network;
8. reports `ready` only when the Data Engine says the website is READY.

Nothing is published during setup. Every check is a contract `verify`
request with `dry_run: true`, and the receiver confirms `persisted: false`.

## Quick start (in the backend process)

```python
from fastapi import FastAPI
from tekjuice_connector import TekJuiceConnector
from tekjuice_connector.publication import PublicationReceiver
from tekjuice_connector.setup import EnvFileSecretStore

app = FastAPI()
secrets = EnvFileSecretStore("/run/tekjuice/tekjuice.env")     # persistent; see below
receiver = PublicationReceiver(store=my_content_store,
                               website_id=os.environ["TEKJUICE_WEBSITE_ID"],
                               secret_store=secrets)

@app.on_event("startup")
def tekjuice_setup():
    # Run it off the request path: the self-check calls this server back.
    threading.Thread(target=lambda: log(TekJuiceConnector().setup(
        receiver=receiver, app=app, secret_store=secrets,
        route_path="/tekjuice/publish",                      # optional; this is the default
    ).to_dict()), daemon=True).start()
```

Configuration, read from the environment or passed as arguments:

| Setting | Meaning |
|---|---|
| `TEKJUICE_WEBSITE_ID`, `TEKJUICE_CONNECTOR_KEY`, `TEKJUICE_DATA_ENGINE_URL` | Onboarding (unchanged) |
| `TEKJUICE_API_BASE_URL` | Public https base URL of **your API**, which may differ from your website's domain |
| `TEKJUICE_RECEIVER_SECRET` | Written by the setup; never set it by hand unless your secret manager owns it |
| `TEKJUICE_PUBLICATION_ENDPOINT_URL` | Written by the setup (base URL + route) |

## Two credentials, never mixed

| Credential | Authenticates | Travels |
|---|---|---|
| Connector key (`TEKJUICE_CONNECTOR_KEY`) | Your backend to the Data Engine, during onboarding | `X-Tek-Juice-Connector-Key` header on `POST /api/v1/connector/handshake` |
| Receiver secret (`TEKJUICE_RECEIVER_SECRET`) | The Data Engine to your receiver, on every publication | `Authorization: Bearer <secret>` on S2S requests; registered once inside the authenticated handshake body |

The receiver never sees the connector key, and the receiver rejects the
connector key as a bearer credential. The receiver secret is generated with
`secrets.token_urlsafe(48)` (48 random bytes, 64 characters). It is never
printed, logged or returned. Results and diagnostics show only a fingerprint
(see below).

## Where the secret is kept

The setup refuses any persistence that would not survive a restart. Writing a
variable into the running Python process is not persistence.

| Deployment | Store | Notes |
|---|---|---|
| Docker Compose `env_file`, systemd `EnvironmentFile`, platform env file | `EnvFileSecretStore(path)` | Atomic write, mode 0600, other lines kept. Refused if the file is tracked by git or not git-ignored. |
| Container with a mounted secrets volume | `FileSecretStore("/run/tekjuice/secret.json")` or `EnvFileSecretStore` on the volume | Refused on the container's own filesystem (overlay root), which a rebuild erases. |
| Secret manager / platform-injected variables | `EnvironmentSecretStore()` (read-only) | The setup uses an injected secret, but cannot create or rotate one. It reports `secret_persistence_unavailable` instead. |
| Vault, AWS Secrets Manager, ... | Your class with `load`, `save`, `lock`, `describe`, `writable` | See `tekjuice_connector.setup.SecretStore`. |

Generation happens under the store's lock, so several worker processes
starting together still produce one secret. A receiver built with
`secret_store=` reloads the store, at most every 5 seconds, when a credential
does not match. Every worker therefore follows the setup and rotations.

### Docker Compose without a secrets volume

The backend's container cannot write the host's `.env`. Run the setup on the
host instead, while the backend is running with the receiver route mounted:

```bash
tekjuice setup --env-file .env --api-base-url https://api.example.com \
               --route-path /api/integrations/tekjuice/s2s/publish
```

On the first run it stores the secret and endpoint in `.env`. The self-check
then fails with `s2s_auth_failed` and `restart_required`, because the running
container does not hold the secret yet. Nothing is registered at that point, so
the two sides never disagree. Restart the backend so it loads `.env`, then run
the same command again. It reuses the stored secret, registers and verifies.
Exit codes: `0` ready, `1` failed, `2` invalid configuration, `3` waiting
(restart or verification).

## The endpoint URL

`TEKJUICE_PUBLICATION_ENDPOINT_URL` = the API base URL + the receiver's route.
The base URL comes, in this order, from:

1. the `api_base_url` argument;
2. `TEKJUICE_API_BASE_URL`;
3. the framework's configuration: FastAPI `servers=[{"url": ...}]`, Flask
   `TEKJUICE_API_BASE_URL` or `SERVER_NAME` with https, Django
   `settings.TEKJUICE_API_BASE_URL`;
4. a previously stored endpoint URL, reused whole.

The website's domain is never used, because your API may live elsewhere. If no
source is available, the setup stops with `api_host_not_discoverable`.

The URL must meet the Data Engine's rules: https, a public DNS hostname, no IP
address, the default port, and no credentials, query or fragment. Placeholders
such as `https://<actual-customer-api-host>/...` are rejected.

Once the Data Engine has *verified* an endpoint, the setup does not move it
elsewhere unless you pass `allow_endpoint_change=True`. This protects against
substitution through a misconfiguration.

## Supported frameworks

| Framework | Finds an existing route | Mounts the route | Base URL source |
|---|---|---|---|
| FastAPI / Starlette (`app`) | yes | yes, at any time | `app.servers` |
| Flask (`app`) | yes | yes, before the app has handled a request | `TEKJUICE_API_BASE_URL`, `SERVER_NAME` |
| Django (`django.conf.settings`) | yes (URLconf) | no: the URLconf is code. The setup gives the exact `path(...)` line to add. | `settings.TEKJUICE_API_BASE_URL` |
| Anything else | pass `route_path=` and serve `adapters.asgi` / `adapters.wsgi` yourself | — | `TEKJUICE_API_BASE_URL` |

An external gateway, reverse proxy or serverless router must route the URL to
the receiver; the setup cannot configure those. The self-check shows whether
it does: `publication_route_missing` means 404/405 or a redirect, and
`s2s_auth_failed` can mean the proxy strips `Authorization`.

**Custom integration:** implement `name`, `matches(app)`, `find_route(app)`,
`mount(app, receiver, path)` (or raise `RouteRegistrationUnavailable` with the
missing step) and `base_url(app)`, then call
`tekjuice_connector.setup.register_integration(...)`.

## Stages and diagnostic codes

`SetupResult.to_dict()` lists each stage with its status (`passed`, `failed`,
`pending`), the evidence it rests on, and the next action. `code` is one of:

`connector_not_configured`, `onboarding_auth_failed`,
`website_ownership_unverified`, `website_scope_mismatch`,
`data_engine_unreachable`, `unsupported_framework`, `adapter_ambiguous`,
`adapter_unavailable`, `api_host_not_discoverable`, `publication_route_missing`,
`receiver_secret_not_configured`, `secret_persistence_unavailable`,
`endpoint_change_unconfirmed`, `endpoint_identity_mismatch`,
`endpoint_registration_failed`, `s2s_auth_failed`,
`publication_contract_incompatible`, `backend_unreachable`,
`publication_capability_unverified`, `integration_ready`.

`connector.diagnostics().to_text()` includes the last setup's stages.
`connector.status()` returns the Data Engine's latest answer.

## Registration evidence and the secret fingerprint

The handshake response includes `publication_endpoint_url`,
`receiver_secret_fingerprint` and `publication_failure_reason`. These need a
Data Engine release that supports connector setup; older ones omit them, and
the setup then relies on the Data Engine's verification alone. The fingerprint
is:

```
sha256(b"tekjuice-receiver-secret:v1:" + secret).hexdigest()[:16]
```

Both sides compute it, so the setup can confirm the Data Engine holds the same
secret without the secret travelling back.

## Rotation and recovery

```python
connector.setup(..., rotate=True)
```

1. A **pending** secret is generated and stored next to the active one. The
   receiver accepts both, so publication continues.
2. The pending secret passes the self-check and is registered. The Data Engine
   re-verifies the connection; until then it pauses publication, usually for a
   few seconds.
3. Only after the Data Engine's verification succeeds does the pending secret
   replace the active one. The old secret then stops working.

If any step fails, both secrets stay accepted and the pending one is kept.
Running `setup(rotate=True)` again **reuses** it, because a retry never
generates yet another secret. To abandon the rotation, call
`setup(rollback_rotation=True)`: it drops the pending secret and registers the
active one again.

A first setup that fails partway is just as safe. The generated secret is
stored before anything is registered, so the next run reuses it.

## Verifying readiness without publishing

`setup()` itself verifies without publishing. Afterwards, `connector.connect()`
or `tekjuice status` reports `publication_ready` from the Data Engine. Neither
call creates content.
