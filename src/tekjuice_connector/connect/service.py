"""Run the connector's endpoint as a service on the customer's server.

systemd (most Linux servers): ``tekjuice-connector.service`` runs
``tekjuice serve`` with ``/etc/tekjuice/connector.env`` (mode 0600, owned by
root, read by systemd) and keeps its receiver secret and articles in
``/var/lib/tekjuice`` (``StateDirectory``, owned by the service user). It
restarts on failure and on boot.
"""

from __future__ import annotations

import getpass
import shlex
import sys
from typing import Dict

SERVICE_NAME = "tekjuice-connector"
ENV_PATH = "/etc/tekjuice/connector.env"
UNIT_PATH = f"/etc/systemd/system/{SERVICE_NAME}.service"
DATA_DIR = "/var/lib/tekjuice"
LISTEN = "127.0.0.1:8787"


def env_file(values: Dict[str, str]) -> str:
    lines = ["# Tek Juice connector service (written by `tekjuice connect`)."]
    for key in sorted(values):
        value = values[key]
        if any(c in value for c in "\n\r"):
            raise ValueError(f"{key} must be one line")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def unit(*, user: str = "", python: str = "") -> str:
    user = user or getpass.getuser()
    command = f"{shlex.quote(python or sys.executable)} -m tekjuice_connector.cli.main serve"
    return f"""[Unit]
Description=Tek Juice connector (S2S publication endpoint and articles)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={user}
EnvironmentFile={ENV_PATH}
ExecStart={command}
StateDirectory=tekjuice
StateDirectoryMode=0700
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""
