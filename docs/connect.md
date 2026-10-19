# `tekjuice connect`: one command, any language

```
pip install tekjuice-connector
tekjuice connect
```

Run it in the backend's directory on the server, after putting the three
values from the Tek Juice setup page in `.env`:

```
TEKJUICE_WEBSITE_ID=...
TEKJUICE_CONNECTOR_KEY=...
TEKJUICE_DATA_ENGINE_URL=https://...
```

(Missing values are asked for, the key without echo, and saved to `.env`
with mode 600; a git-tracked `.env` is never written.)

## What it does

1. **Authenticates** with the Data Engine (handshake). A website that is
   already connected and READY is left alone.
2. **Detects the server**: the application's language and framework (Python,
   JavaScript/Node, PHP, Ruby, Go, Java, .NET, Elixir, Rust, …), the web server
   (nginx, Caddy, Apache) and systemd. Informational only: what it installs is
   the same whatever the application is written in.
3. **Asks for what it cannot discover** (defaults proposed from the website):
   - the public HTTPS address where Tek Juice reaches this server
     (`TEKJUICE_API_BASE_URL`, e.g. `https://www.example.com`);
   - where published articles appear (`TEKJUICE_ARTICLES_URL`, e.g.
     `https://www.example.com/articles`).
4. **Uses an existing receiver** when the in-process connector already serves
   `/tekjuice/publish` there (then it completes that receiver's setup).
5. **Otherwise creates the endpoint**, without touching the application's code:
   - the connector runs beside the application as the systemd service
     `tekjuice-connector` (`127.0.0.1:8787`, settings in
     `/etc/tekjuice/connector.env`, secret and articles in `/var/lib/tekjuice`);
   - it generates the receiver secret, keeps published articles itself and
     serves them (the site's own look: its homepage is the page shell);
   - nginx: one `include /etc/nginx/tekjuice/<host>.conf;` line is added to the
     site's HTTPS server block, routing only `/tekjuice/publish` and the
     articles path to the connector. The edited file is backed up; if
     `nginx -t` fails the backup is restored and nothing is reloaded. Caddy and
     Apache: the exact configuration is printed.
6. **Registers and waits for verification** until the website is READY.

It asks before changing anything (`--yes` accepts the proposals). Without
administrator rights or systemd it changes nothing and prints the exact
steps instead. Secrets are never printed.

Exit codes: `0` connected, `1` failed, `2` invalid input, `3` waiting for a
step that needs you (the service keeps retrying on its own).

## Afterwards

| | |
|---|---|
| Status | `curl -s http://127.0.0.1:8787/readyz` |
| Logs | `sudo journalctl -u tekjuice-connector` |
| Re-check / update | `tekjuice connect` again (idempotent) |
| Disconnect | remove the `include /etc/nginx/tekjuice/…` line, reload nginx, `sudo systemctl disable --now tekjuice-connector` |

Applications that want articles inside their own pages or database can still
use the in-process connector (Python, docs/setup.md) or implement the content
API for the sidecar (docs/sidecar.md); `tekjuice connect` detects an existing
receiver and keeps it.
