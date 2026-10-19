"""Performance controls: approved, reversible delivery settings (``performance.*``).

The Data Engine measures the site with PageSpeed Insights. Some findings are
delivery settings rather than code: a long-lived cache policy for versioned
static assets, or compression of text responses. When the site installs this
module's middleware around its application, those settings can be changed by
the Data Engine through the existing S2S receiver, without touching the
site's code, templates or content.

What is genuinely implemented (and therefore the only thing advertised):

``performance.static_cache`` (v1)
    ``Cache-Control: public, max-age=N, immutable`` on responses the
    middleware sees that are *versioned static assets*: GET/HEAD, status
    200, a static file extension, a content-hashed file name, under one of
    the approved path prefixes. Never on HTML, JSON/API responses, responses
    that set cookies, requests that carry credentials, or responses whose
    application already declared ``private``/``no-store``.

``performance.compression`` (v1)
    gzip (and Brotli when the ``brotli`` package is installed) for
    compressible responses the client accepts, that are not already encoded,
    not partial and not ``no-transform``; ``Vary: Accept-Encoding`` is added
    and strong ETags become weak.

``performance.snapshot`` (v1)
    Every change returns the previous configuration; ``restore`` puts a
    snapshot back. Repeated idempotency keys replay the earlier result.

Responses that never pass through the middleware (files served directly by
a web server or CDN in front of the application) are out of its reach. The
middleware therefore records which path prefixes it has actually served
static assets for, and the capability discovery reports them, so the Data
Engine can tell whether an affected asset is controllable before proposing
a change.

Not implemented, reported as unsupported: image transformation services
and CDN configuration (``performance.image_delivery``, ``performance.cdn``).

Request (contract 1.0, ``action: "performance_change"``)::

    {"contract_version": "1.0", "action": "performance_change", "website_id": "...",
     "idempotency_key": "...",
     "change": {"type": "static_cache", "enabled": true, "max_age": 31536000,
                "path_prefixes": ["/assets/"]}}

    {"change": {"type": "compression", "enabled": true, "min_bytes": 1024}}
    {"change": {"type": "restore", "snapshot": {"static_cache": null, "compression": null}}}

Capability discovery (``action: "capabilities"``) returns the versioned
capabilities, their scope and the unsupported ones with reasons.
"""

from __future__ import annotations

import copy
import gzip
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from tekjuice_connector.publication.errors import ContractViolationError, UnsupportedCapabilityError

ACTION_PERFORMANCE_CHANGE = "performance_change"
ACTION_CAPABILITIES = "capabilities"
CAPABILITY_STATIC_CACHE = "performance.static_cache"
CAPABILITY_COMPRESSION = "performance.compression"
CAPABILITY_SNAPSHOT = "performance.snapshot"
CAPABILITY_IMAGE_DELIVERY = "performance.image_delivery"
CAPABILITY_CDN = "performance.cdn"
CAPABILITY_VERSION = "1"
DISCOVERY_VERSION = "1"

CHANGE_TYPES = ("static_cache", "compression", "restore")
UNSUPPORTED_TYPES = {"image_delivery": CAPABILITY_IMAGE_DELIVERY, "cdn": CAPABILITY_CDN}
UNSUPPORTED_REASONS = {
    CAPABILITY_IMAGE_DELIVERY: "no image optimisation service is configured for this connector",
    CAPABILITY_CDN: "no CDN integration is configured for this connector",
}
MAX_AGE_MIN, MAX_AGE_MAX = 3600, 31536000
MIN_BYTES_MIN, MIN_BYTES_MAX = 256, 1024 * 1024
MAX_PREFIXES = 10
MAX_BUFFER_BYTES = 8 * 1024 * 1024
MAX_REPLAYS = 50
STATIC_EXTENSIONS = ("js", "mjs", "css", "woff", "woff2", "ttf", "otf", "eot", "png", "jpg", "jpeg", "gif",
                     "webp", "avif", "svg", "ico", "map")
_PREFIX = re.compile(r"^/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*/$")
# A content hash in the file name: name-HASH.ext or name.HASH.ext.
_HASHED = re.compile(r"^.+[.-]([A-Za-z0-9_]{8,64})\.(" + "|".join(STATIC_EXTENSIONS) + r")$")
_CAMEL_WORD = re.compile(r"^(?:[A-Z][a-z]{2,})+$")
_COMPRESSIBLE = ("text/", "application/javascript", "application/x-javascript", "application/json",
                 "application/ld+json", "application/manifest+json", "application/xml", "application/rss+xml",
                 "application/atom+xml", "image/svg+xml", "application/wasm", "font/ttf", "font/otf")
_NEVER_CACHE_TYPES = ("text/html", "application/json", "application/ld+json", "application/xml")
_REQUEST_FIELDS = frozenset({"contract_version", "action", "website_id", "idempotency_key", "change"})

try:  # Brotli is optional; only advertised when importable.
    import brotli as _brotli  # type: ignore
except Exception:  # noqa: BLE001
    _brotli = None


def encodings() -> List[str]:
    return (["br"] if _brotli is not None else []) + ["gzip"]


def is_versioned_asset(path: str) -> bool:
    """A static file whose name carries a content hash (so it can be cached forever)."""
    name = path.rsplit("/", 1)[-1]
    match = _HASHED.match(name)
    if not match:
        return False
    token = match.group(1)
    # A hash has a digit, or mixes cases without being a CamelCase word.
    if any(c.isdigit() for c in token):
        return True
    return token != token.lower() and token != token.upper() and not _CAMEL_WORD.match(token)


def _empty() -> Dict[str, Any]:
    return {"static_cache": None, "compression": None}


# ── Configuration storage ─────────────────────────────────────────────────


class PerformanceControls:
    """The approved performance configuration and the middleware applying it.

    ``state_path``: a JSON file holding the configuration (atomic writes;
    reloaded when another process changes it). Without it the configuration
    lives in memory and is lost on restart (tests, single process).
    """

    def __init__(self, state_path: Optional[str] = None) -> None:
        self._path = state_path
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {"config": _empty(), "replays": {}, "version": 0}
        self._mtime = 0.0
        self._checked = 0.0
        self._installed: List[str] = []
        self._observed: Dict[str, int] = {}
        if state_path and os.path.exists(state_path):
            self._load()

    # Installation ---------------------------------------------------------
    @property
    def installed(self) -> bool:
        """True once the middleware wraps an application in this process."""
        return bool(self._installed)

    def asgi(self, app: Any) -> Any:
        """Wrap an ASGI application (FastAPI, Starlette, Django ASGI, ...)."""
        self._installed.append("asgi")
        return PerformanceASGIMiddleware(app, self)

    def wsgi(self, app: Any) -> Any:
        """Wrap a WSGI application (Flask, Django WSGI, ...)."""
        self._installed.append("wsgi")
        return PerformanceWSGIMiddleware(app, self)

    # State ------------------------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._mtime = os.path.getmtime(self._path)
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and isinstance(data.get("config"), dict):
            self._state = {"config": {**_empty(), **data["config"]}, "replays": data.get("replays") or {},
                           "version": int(data.get("version") or 0)}

    def _refresh(self) -> None:
        if not self._path:
            return
        now = time.monotonic()
        if now - self._checked < 1.0:
            return
        self._checked = now
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            return
        if mtime != self._mtime:
            self._load()

    def _save(self) -> None:
        if not self._path:
            return
        directory = os.path.dirname(os.path.abspath(self._path))
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tekjuice-performance-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
            self._mtime = os.path.getmtime(self._path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def config(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh()
            return copy.deepcopy(self._state["config"])

    def observe(self, path: str) -> None:
        """Record the directory prefix of a static asset this middleware served."""
        prefix = path.rsplit("/", 1)[0] + "/"
        if len(self._observed) < 200 or prefix in self._observed:
            self._observed[prefix] = self._observed.get(prefix, 0) + 1

    # Discovery --------------------------------------------------------------
    def capabilities(self) -> Tuple[str, ...]:
        if not self.installed:
            return ()
        return (CAPABILITY_STATIC_CACHE, CAPABILITY_COMPRESSION, CAPABILITY_SNAPSHOT)

    def describe(self) -> Dict[str, Any]:
        """Versioned capability details (what each can do, and where)."""
        if not self.installed:
            return {"capabilities": {}, "unsupported": {
                CAPABILITY_STATIC_CACHE: "the performance middleware is not installed around the application",
                CAPABILITY_COMPRESSION: "the performance middleware is not installed around the application",
                CAPABILITY_SNAPSHOT: "the performance middleware is not installed around the application",
                **UNSUPPORTED_REASONS}}
        scope = {"applies_to": "responses served through the application's "
                               f"{'/'.join(sorted(set(self._installed)))} middleware",
                 "observed_static_prefixes": sorted(self._observed)[:50],
                 "persistent": bool(self._path)}
        return {
            "capabilities": {
                CAPABILITY_STATIC_CACHE: {
                    "version": CAPABILITY_VERSION, **scope,
                    "max_age_range": [MAX_AGE_MIN, MAX_AGE_MAX],
                    "rules": ["GET/HEAD 200 only", "content-hashed file names only",
                              "approved path prefixes only", "never text/html or JSON",
                              "never responses with Set-Cookie", "never requests with Authorization",
                              "never overrides private or no-store"]},
                CAPABILITY_COMPRESSION: {
                    "version": CAPABILITY_VERSION, **scope, "encodings": encodings(),
                    "min_bytes_range": [MIN_BYTES_MIN, MIN_BYTES_MAX],
                    "rules": ["respects Accept-Encoding", "never re-encodes", "skips partial and no-transform",
                              f"buffers at most {MAX_BUFFER_BYTES} bytes"]},
                CAPABILITY_SNAPSHOT: {"version": CAPABILITY_VERSION, "restore": True, "idempotent": True},
            },
            "unsupported": dict(UNSUPPORTED_REASONS),
        }

    # Changes ----------------------------------------------------------------
    def apply(self, request: "PerformanceChangeRequest") -> Dict[str, Any]:
        with self._lock:
            self._refresh()
            replay = self._state["replays"].get(request.idempotency_key)
            if replay:
                return {**replay, "replayed": True}
            previous = copy.deepcopy(self._state["config"])
            current = copy.deepcopy(previous)
            change = request.change
            if change["type"] == "restore":
                current = change["snapshot"]
            else:
                current[change["type"]] = change["settings"] if change["settings"]["enabled"] else None
            self._state["config"] = current
            self._state["version"] += 1
            body = {"status": "applied", "replayed": False, "type": change["type"], "previous": previous,
                    "current": copy.deepcopy(current), "config_version": self._state["version"]}
            replays = self._state["replays"]
            replays[request.idempotency_key] = body
            while len(replays) > MAX_REPLAYS:
                replays.pop(next(iter(replays)))
            self._save()
            return body


# ── Request validation ────────────────────────────────────────────────────


@dataclass(frozen=True)
class PerformanceChangeRequest:
    idempotency_key: str
    change: Dict[str, Any]


def _static_cache(change: Dict[str, Any]) -> Dict[str, Any]:
    if change.get("enabled") is False:
        return {"enabled": False}
    max_age = change.get("max_age", MAX_AGE_MAX)
    if not isinstance(max_age, int) or isinstance(max_age, bool) or not MAX_AGE_MIN <= max_age <= MAX_AGE_MAX:
        raise ContractViolationError(f"'change.max_age' must be between {MAX_AGE_MIN} and {MAX_AGE_MAX} seconds")
    prefixes = change.get("path_prefixes")
    if not isinstance(prefixes, list) or not 0 < len(prefixes) <= MAX_PREFIXES:
        raise ContractViolationError(f"'change.path_prefixes' must list 1 to {MAX_PREFIXES} directories")
    for prefix in prefixes:
        if not isinstance(prefix, str) or not _PREFIX.match(prefix) or prefix == "/" or ".." in prefix:
            raise ContractViolationError("'change.path_prefixes' must be directories such as '/assets/' (not '/')")
    return {"enabled": True, "max_age": max_age, "path_prefixes": sorted(set(prefixes))}


def _compression(change: Dict[str, Any]) -> Dict[str, Any]:
    if change.get("enabled") is False:
        return {"enabled": False}
    min_bytes = change.get("min_bytes", 1024)
    if not isinstance(min_bytes, int) or isinstance(min_bytes, bool) or not MIN_BYTES_MIN <= min_bytes <= MIN_BYTES_MAX:
        raise ContractViolationError(f"'change.min_bytes' must be between {MIN_BYTES_MIN} and {MIN_BYTES_MAX}")
    return {"enabled": True, "min_bytes": min_bytes, "encodings": encodings()}


def _snapshot(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"static_cache", "compression"}:
        raise ContractViolationError("'change.snapshot' must be a configuration returned as 'previous'")
    out = _empty()
    if value.get("static_cache"):
        out["static_cache"] = _static_cache({**value["static_cache"], "enabled": True})
    if value.get("compression"):
        out["compression"] = _compression({**value["compression"], "enabled": True})
    return out


def parse_performance_change(data: Dict[str, Any]) -> PerformanceChangeRequest:
    unknown = set(data) - _REQUEST_FIELDS
    if unknown:
        raise ContractViolationError(f"Unknown request field(s): {', '.join(sorted(unknown))}")
    key = data.get("idempotency_key")
    if not isinstance(key, str) or not 8 <= len(key) <= 300:
        raise ContractViolationError("'idempotency_key' is required")
    change = data.get("change")
    if not isinstance(change, dict):
        raise ContractViolationError("'change' must be an object")
    kind = change.get("type")
    if kind in UNSUPPORTED_TYPES:
        capability = UNSUPPORTED_TYPES[kind]
        raise UnsupportedCapabilityError(f"'{kind}' is not supported: {UNSUPPORTED_REASONS[capability]}",
                                         capability=capability)
    if kind not in CHANGE_TYPES:
        raise ContractViolationError(f"'change.type' must be one of {', '.join(CHANGE_TYPES)}")
    allowed = {"static_cache": {"type", "enabled", "max_age", "path_prefixes"},
               "compression": {"type", "enabled", "min_bytes"}, "restore": {"type", "snapshot"}}[kind]
    if set(change) - allowed:
        raise ContractViolationError(f"Unknown change field(s): {', '.join(sorted(set(change) - allowed))}")
    if kind == "restore":
        return PerformanceChangeRequest(key, {"type": "restore", "snapshot": _snapshot(change.get("snapshot"))})
    settings = _static_cache(change) if kind == "static_cache" else _compression(change)
    return PerformanceChangeRequest(key, {"type": kind, "settings": settings})


def required_capability(change: Dict[str, Any]) -> str:
    return {"static_cache": CAPABILITY_STATIC_CACHE, "compression": CAPABILITY_COMPRESSION}.get(
        change["type"], CAPABILITY_SNAPSHOT)


# ── Response rules (shared by both middlewares) ───────────────────────────


def _header(headers: Iterable[Tuple[str, str]], name: str) -> Optional[str]:
    for key, value in headers:
        if key.lower() == name:
            return value
    return None


def cache_control_for(config: Dict[str, Any], *, method: str, path: str, status: int,
                      request_headers: Iterable[Tuple[str, str]],
                      response_headers: Iterable[Tuple[str, str]]) -> Optional[str]:
    """The Cache-Control to set, or None to leave the response alone."""
    rule = config.get("static_cache")
    if not rule or method not in ("GET", "HEAD") or status != 200:
        return None
    if not any(path.startswith(p) for p in rule["path_prefixes"]) or not is_versioned_asset(path):
        return None
    response_headers, request_headers = list(response_headers), list(request_headers)
    content_type = (_header(response_headers, "content-type") or "").lower()
    if any(content_type.startswith(t) for t in _NEVER_CACHE_TYPES):
        return None
    if _header(response_headers, "set-cookie") is not None or _header(request_headers, "authorization"):
        return None
    existing = (_header(response_headers, "cache-control") or "").lower()
    if "private" in existing or "no-store" in existing:
        return None
    return f"public, max-age={rule['max_age']}, immutable"


def may_compress(config: Dict[str, Any], *, method: str, status: int,
                 request_headers: Iterable[Tuple[str, str]],
                 response_headers: Iterable[Tuple[str, str]]) -> Optional[str]:
    """The encoding to use if the body turns out large enough (decided from headers alone)."""
    rule = config.get("compression")
    if not rule or method == "HEAD" or status != 200:
        return None
    response_headers = list(response_headers)
    if _header(response_headers, "content-encoding") or _header(response_headers, "content-range"):
        return None
    if "no-transform" in (_header(response_headers, "cache-control") or "").lower():
        return None
    content_type = (_header(response_headers, "content-type") or "").lower()
    if content_type.startswith("text/event-stream") or not any(content_type.startswith(t) for t in _COMPRESSIBLE):
        return None
    length = _header(response_headers, "content-length")
    if length is not None and length.isdigit() and int(length) < rule["min_bytes"]:
        return None
    accepted = _accepted_encodings(_header(list(request_headers), "accept-encoding") or "")
    for encoding in rule.get("encodings") or ["gzip"]:
        if encoding in accepted and (encoding != "br" or _brotli is not None):
            return encoding
    return None


def choose_encoding(config: Dict[str, Any], *, method: str, status: int, size: int,
                    request_headers: Iterable[Tuple[str, str]],
                    response_headers: Iterable[Tuple[str, str]]) -> Optional[str]:
    encoding = may_compress(config, method=method, status=status, request_headers=request_headers,
                            response_headers=response_headers)
    return encoding if encoding and size >= config["compression"]["min_bytes"] else None


def _accepted_encodings(value: str) -> set:
    out = set()
    for part in value.split(","):
        name, _, params = part.strip().partition(";")
        q = 1.0
        for param in params.split(";"):
            key, _, number = param.strip().partition("=")
            if key == "q":
                try:
                    q = float(number)
                except ValueError:
                    q = 0.0
        if name and q > 0:
            out.add(name.strip().lower())
    return out


def compress(body: bytes, encoding: str) -> bytes:
    if encoding == "br":
        return _brotli.compress(body)
    return gzip.compress(body, compresslevel=6, mtime=0)


def rewrite_headers(headers: List[Tuple[str, str]], *, cache_control: Optional[str],
                    encoding: Optional[str], length: Optional[int]) -> List[Tuple[str, str]]:
    out = []
    vary_seen = False
    for key, value in headers:
        lower = key.lower()
        if cache_control and lower == "cache-control":
            continue
        if encoding and lower == "content-length":
            continue
        if encoding and lower == "etag" and not value.startswith("W/"):
            value = "W/" + value
        if encoding and lower == "vary":
            vary_seen = True
            if "accept-encoding" not in value.lower() and value.strip() != "*":
                value = f"{value}, Accept-Encoding"
        out.append((key, value))
    if cache_control:
        out.append(("Cache-Control", cache_control))
    if encoding:
        out.append(("Content-Encoding", encoding))
        out.append(("Content-Length", str(length)))
        if not vary_seen:
            out.append(("Vary", "Accept-Encoding"))
    return out


# ── ASGI ──────────────────────────────────────────────────────────────────


def _decode(headers: Iterable[Tuple[bytes, bytes]]) -> List[Tuple[str, str]]:
    return [(k.decode("latin-1"), v.decode("latin-1")) for k, v in headers]


def _encode(headers: Iterable[Tuple[str, str]]) -> List[Tuple[bytes, bytes]]:
    return [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]


class PerformanceASGIMiddleware:
    def __init__(self, app: Any, controls: PerformanceControls) -> None:
        self.app = app
        self.controls = controls

    async def __call__(self, scope: Dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        config = self.controls.config()
        if not config.get("static_cache") and not config.get("compression") and not _static_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        method, path = scope.get("method", "GET"), scope.get("path", "/")
        request_headers = _decode(scope.get("headers") or [])
        state: Dict[str, Any] = {"start": None, "chunks": [], "size": 0, "passthrough": False}

        async def wrapped(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = _decode(message.get("headers") or [])
                status = message["status"]
                if status == 200 and _static_path(path):
                    self.controls.observe(path)
                cache = cache_control_for(config, method=method, path=path, status=status,
                                          request_headers=request_headers, response_headers=headers)
                if cache:
                    headers = rewrite_headers(headers, cache_control=cache, encoding=None, length=None)
                state["start"] = {**message, "headers": headers}
                if not may_compress(config, method=method, status=status, request_headers=request_headers,
                                    response_headers=headers):
                    state["passthrough"] = True
                    await send({**message, "headers": _encode(headers)})
                return
            if message["type"] != "http.response.body" or state["passthrough"]:
                await send(message)
                return
            state["chunks"].append(message.get("body", b""))
            state["size"] += len(state["chunks"][-1])
            more = message.get("more_body", False)
            if state["size"] > MAX_BUFFER_BYTES:
                state["passthrough"] = True
                await send({**state["start"], "headers": _encode(state["start"]["headers"])})
                await send({"type": "http.response.body", "body": b"".join(state["chunks"]), "more_body": more})
                return
            if more:
                return
            body = b"".join(state["chunks"])
            start = state["start"]
            encoding = choose_encoding(config, method=method, status=start["status"], size=len(body),
                                       request_headers=request_headers, response_headers=start["headers"])
            headers = start["headers"]
            if encoding:
                body = compress(body, encoding)
                headers = rewrite_headers(headers, cache_control=None, encoding=encoding, length=len(body))
            await send({**start, "headers": _encode(headers)})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, wrapped)


def _static_path(path: str) -> bool:
    return bool(re.search(r"\.(" + "|".join(STATIC_EXTENSIONS) + r")$", path.rsplit("/", 1)[-1]))


# ── WSGI ──────────────────────────────────────────────────────────────────


class PerformanceWSGIMiddleware:
    def __init__(self, app: Callable, controls: PerformanceControls) -> None:
        self.app = app
        self.controls = controls

    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        config = self.controls.config()
        method = environ.get("REQUEST_METHOD", "GET")
        path = environ.get("PATH_INFO", "/") or "/"
        if not config.get("static_cache") and not config.get("compression") and not _static_path(path):
            return self.app(environ, start_response)
        request_headers = [(k[5:].replace("_", "-").lower(), v) for k, v in environ.items() if k.startswith("HTTP_")]
        captured: Dict[str, Any] = {"written": []}

        def capture(status: str, headers: List[Tuple[str, str]], exc_info: Any = None) -> Callable:
            captured.update(status=status, headers=list(headers), exc_info=exc_info)
            return captured["written"].append

        result = self.app(environ, capture)
        iterator = iter(result)
        first = next(iterator, b"")   # a generator app calls start_response here
        code = int(captured["status"].split(" ", 1)[0])
        headers = captured["headers"]
        if code == 200 and _static_path(path):
            self.controls.observe(path)
        cache = cache_control_for(config, method=method, path=path, status=code,
                                  request_headers=request_headers, response_headers=headers)
        encoding = may_compress(config, method=method, status=code, request_headers=request_headers,
                                response_headers=headers)
        head = captured["written"] + [first]
        if not encoding:
            start_response(captured["status"], rewrite_headers(headers, cache_control=cache, encoding=None,
                                                               length=None), captured.get("exc_info"))
            return _Chained(head, iterator, result)
        body, size = list(head), sum(len(c) for c in head)
        for chunk in iterator:
            body.append(chunk)
            size += len(chunk)
            if size > MAX_BUFFER_BYTES:
                start_response(captured["status"], rewrite_headers(headers, cache_control=cache, encoding=None,
                                                                   length=None), captured.get("exc_info"))
                return _Chained(body, iterator, result)
        if hasattr(result, "close"):
            result.close()
        data = b"".join(body)
        if size < config["compression"]["min_bytes"]:
            encoding = None
        else:
            data = compress(data, encoding)
        start_response(captured["status"], rewrite_headers(headers, cache_control=cache, encoding=encoding,
                                                           length=len(data) if encoding else None),
                       captured.get("exc_info"))
        return [data]


class _Chained:
    """Already-read chunks, then the rest of the application's iterable (closed after)."""

    def __init__(self, head: List[bytes], rest: Any, result: Any) -> None:
        self._head, self._rest, self._result = head, rest, result

    def __iter__(self):
        for chunk in self._head:
            if chunk:
                yield chunk
        for chunk in self._rest:
            yield chunk

    def close(self) -> None:
        if hasattr(self._result, "close"):
            self._result.close()
