"""Where the connector keeps the receiver secret.

The setup generates the receiver secret on the customer's server and needs
somewhere durable to keep it. The right place depends on how the backend is
deployed, so persistence is a small interface with three implementations:

``EnvFileSecretStore``
    A dotenv file the deployment loads (``docker compose`` ``env_file``,
    systemd ``EnvironmentFile``, a platform's mounted env file). Writes are
    atomic, the file is kept at mode 0600, other lines are preserved, and a
    file tracked by git (or not git-ignored) is refused.
``FileSecretStore``
    A JSON file on a persistent volume (e.g. a mounted secrets volume).
``EnvironmentSecretStore``
    Read-only: secrets injected by a secret manager or the platform. The
    setup can use a secret it finds but cannot create or rotate one; it says
    so instead of pretending a process-local variable was persisted.

Custom stores (Vault, AWS Secrets Manager, ...) implement the same
``load`` / ``save`` / ``lock`` / ``describe`` methods (see ``SecretStore``).

Inside a container, writable stores refuse paths on the container's own
filesystem: a value written there disappears when the container is rebuilt,
and the Data Engine would be left holding a secret the receiver forgot.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Protocol, runtime_checkable

from tekjuice_connector.exceptions.configuration import ConfigurationError
from tekjuice_connector.security.secrets import SecretStr

ENV_RECEIVER_SECRET = "TEKJUICE_RECEIVER_SECRET"
ENV_RECEIVER_SECRET_PENDING = "TEKJUICE_RECEIVER_SECRET_PENDING"
ENV_PUBLICATION_ENDPOINT_URL = "TEKJUICE_PUBLICATION_ENDPOINT_URL"


class SecretPersistenceError(ConfigurationError):
    """The secret cannot be stored safely in this environment."""


@dataclass(frozen=True)
class ReceiverSecrets:
    """The receiver's secrets: ``active`` (what the Data Engine holds) and,
    while a rotation is in progress, ``pending`` (the replacement)."""

    active: Optional[SecretStr] = None
    pending: Optional[SecretStr] = None
    endpoint_url: Optional[str] = None

    def __repr__(self) -> str:
        return (f"ReceiverSecrets(active={'set' if self.active else None}, "
                f"pending={'set' if self.pending else None}, endpoint_url={self.endpoint_url!r})")


@runtime_checkable
class SecretStore(Protocol):
    """Persistence for the receiver secret (and the endpoint URL)."""

    #: False for read-only stores: the setup cannot create or rotate secrets.
    writable: bool

    def load(self) -> Optional[ReceiverSecrets]: ...

    def save(self, secrets: ReceiverSecrets) -> None: ...

    def lock(self) -> "contextlib.AbstractContextManager[None]":
        """Exclusive lock across processes (generate-if-absent must not race)."""
        ...

    def describe(self) -> Dict[str, object]:
        """Secret-free description for diagnostics."""
        ...


# ── Environment checks ───────────────────────────────────────────────────────

def in_container() -> bool:
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return False
    return any(marker in text for marker in ("docker", "kubepods", "containerd", "libpod"))


def _mount_for(path: Path) -> Optional[Dict[str, str]]:
    """The mount holding *path* (longest mount point prefix), from mountinfo."""
    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    target = str(path.resolve())
    best: Optional[Dict[str, str]] = None
    for line in lines:
        left, _, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if len(fields) < 5 or not tail:
            continue
        mount_point = fields[4]
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            if best is None or len(mount_point) > len(best["mount_point"]):
                best = {"mount_point": mount_point, "fstype": tail[0]}
    return best


def ephemeral_reason(path: Path) -> Optional[str]:
    """Why a file at *path* would not survive a container rebuild, or None."""
    if not in_container():
        return None
    mount = _mount_for(path.parent if not path.exists() else path)
    if mount is None:
        return None
    if mount["mount_point"] == "/" and mount["fstype"] in ("overlay", "aufs", "overlayfs", "fuse-overlayfs"):
        return (f"{path} is on the container's own filesystem ({mount['fstype']}); it would be lost when the "
                "container is recreated. Mount a volume for it, or run the setup where the deployment's "
                "env file lives.")
    return None


def git_exposure(path: Path) -> Optional[str]:
    """Why writing a secret to *path* risks committing it, or None."""
    path = path.absolute()
    directory = path.parent
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    try:
        top = subprocess.run(["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0 or not top.stdout.strip():
        return None
    root = Path(top.stdout.strip()).resolve()
    try:
        relative = str(path.resolve().relative_to(root))
    except ValueError:
        return None
    tracked = subprocess.run(["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
                             capture_output=True, text=True, timeout=5)
    if tracked.returncode == 0:
        return f"{path} is tracked by git; a secret written there would be committed"
    ignored = subprocess.run(["git", "-C", str(root), "check-ignore", "-q", "--", relative],
                             capture_output=True, text=True, timeout=5)
    if ignored.returncode != 0:
        return f"{path} is inside a git work tree and not git-ignored; add it to .gitignore first"
    return None


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - non-POSIX platforms
            pass
        yield
    finally:
        os.close(fd)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def _writable_checks(path: Path, allow_ephemeral: bool, allow_untracked_check: bool) -> None:
    reason = None if allow_ephemeral else ephemeral_reason(path)
    if reason:
        raise SecretPersistenceError(reason)
    exposure = git_exposure(path) if allow_untracked_check else None
    if exposure:
        raise SecretPersistenceError(exposure)


# ── Stores ───────────────────────────────────────────────────────────────────

def _quote(value: str) -> str:
    return value if all(c.isalnum() or c in "-_.:/@%+=" for c in value) else json.dumps(value)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return json.loads(value) if value[0] == '"' else value[1:-1]
    return value


class EnvFileSecretStore:
    """Receiver secret in a dotenv file the deployment loads at start-up."""

    writable = True

    def __init__(self, path: "os.PathLike[str] | str", *, allow_ephemeral: bool = False,
                 check_git: bool = True) -> None:
        self.path = Path(path)
        self._allow_ephemeral = allow_ephemeral
        self._check_git = check_git

    def _read(self) -> List[str]:
        try:
            return self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

    def _values(self) -> Dict[str, str]:
        values: Dict[str, str] = {}
        for line in self._read():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.removeprefix("export ").partition("=")
            values[key.strip()] = _unquote(value)
        return values

    def load(self) -> Optional[ReceiverSecrets]:
        values = self._values()
        active, pending = values.get(ENV_RECEIVER_SECRET), values.get(ENV_RECEIVER_SECRET_PENDING)
        if not (active or pending or values.get(ENV_PUBLICATION_ENDPOINT_URL)):
            return None
        return ReceiverSecrets(active=SecretStr(active) if active else None,
                               pending=SecretStr(pending) if pending else None,
                               endpoint_url=values.get(ENV_PUBLICATION_ENDPOINT_URL) or None)

    def save(self, secrets: ReceiverSecrets) -> None:
        _writable_checks(self.path, self._allow_ephemeral, self._check_git)
        wanted = {
            ENV_RECEIVER_SECRET: secrets.active.reveal() if secrets.active else None,
            ENV_RECEIVER_SECRET_PENDING: secrets.pending.reveal() if secrets.pending else None,
            ENV_PUBLICATION_ENDPOINT_URL: secrets.endpoint_url,
        }
        lines, seen = [], set()
        for line in self._read():
            key = line.strip().removeprefix("export ").partition("=")[0].strip()
            if key in wanted:
                seen.add(key)
                if wanted[key]:
                    lines.append(f"{key}={_quote(wanted[key])}")
                continue
            lines.append(line)
        for key, value in wanted.items():
            if key not in seen and value:
                lines.append(f"{key}={_quote(value)}")
        _atomic_write(self.path, "\n".join(lines) + "\n")

    def lock(self):
        return _file_lock(self.path)

    def describe(self) -> Dict[str, object]:
        return {"store": "env_file", "path": str(self.path), "writable": True,
                "exists": self.path.exists()}


class FileSecretStore:
    """Receiver secret in a 0600 JSON file on a persistent volume."""

    writable = True

    def __init__(self, path: "os.PathLike[str] | str", *, allow_ephemeral: bool = False) -> None:
        self.path = Path(path)
        self._allow_ephemeral = allow_ephemeral

    def load(self) -> Optional[ReceiverSecrets]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise SecretPersistenceError(f"{self.path} is not valid JSON") from exc
        return ReceiverSecrets(
            active=SecretStr(data["active"]) if data.get("active") else None,
            pending=SecretStr(data["pending"]) if data.get("pending") else None,
            endpoint_url=data.get("endpoint_url") or None,
        )

    def save(self, secrets: ReceiverSecrets) -> None:
        _writable_checks(self.path, self._allow_ephemeral, True)
        data = {
            "active": secrets.active.reveal() if secrets.active else None,
            "pending": secrets.pending.reveal() if secrets.pending else None,
            "endpoint_url": secrets.endpoint_url,
        }
        _atomic_write(self.path, json.dumps(data))

    def lock(self):
        return _file_lock(self.path)

    def describe(self) -> Dict[str, object]:
        return {"store": "file", "path": str(self.path), "writable": True, "exists": self.path.exists()}


class EnvironmentSecretStore:
    """Read-only: secrets injected into the process environment by a secret
    manager or the platform."""

    writable = False

    def __init__(self, environ: Optional[Dict[str, str]] = None) -> None:
        self._environ = environ

    def _env(self) -> Dict[str, str]:
        return dict(os.environ if self._environ is None else self._environ)

    def load(self) -> Optional[ReceiverSecrets]:
        env = self._env()
        active, pending = env.get(ENV_RECEIVER_SECRET), env.get(ENV_RECEIVER_SECRET_PENDING)
        if not (active or pending or env.get(ENV_PUBLICATION_ENDPOINT_URL)):
            return None
        return ReceiverSecrets(active=SecretStr(active) if active else None,
                               pending=SecretStr(pending) if pending else None,
                               endpoint_url=env.get(ENV_PUBLICATION_ENDPOINT_URL) or None)

    def save(self, secrets: ReceiverSecrets) -> None:
        raise SecretPersistenceError(
            "The receiver secret comes from the process environment, which the connector cannot persist: "
            f"set {ENV_RECEIVER_SECRET} in your secret manager or deployment configuration, or configure an "
            "EnvFileSecretStore / FileSecretStore the deployment loads."
        )

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        yield

    def describe(self) -> Dict[str, object]:
        return {"store": "environment", "writable": False}
