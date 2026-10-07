"""Environment variable loading utilities.

Reads raw string values from the process environment.
Optional .env file loading is supported via python-dotenv when it is
installed, but the package does NOT require it.  If dotenv is absent the
environment is used as-is.
"""

from __future__ import annotations

import os
from typing import Optional

from tekjuice_connector.models.common import (
    ENV_CONNECTOR_KEY,
    ENV_DATA_ENGINE_URL,
    ENV_WEBSITE_ID,
)


def _try_load_dotenv() -> None:
    """Attempt to load a .env file if python-dotenv is installed.

    This is deliberately best-effort: no error is raised when dotenv is
    absent so the package stays dependency-free in production.
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import]

        load_dotenv(override=False)
    except ImportError:
        pass


def load_env(load_dotenv_file: bool = True) -> dict[str, Optional[str]]:
    """Return a dict of the three Connector environment variables.

    Parameters
    ----------
    load_dotenv_file:
        When ``True`` (default) a .env file is loaded via python-dotenv
        if that package is installed.  Set to ``False`` to skip the
        attempt entirely (useful in tests).

    Returns
    -------
    dict with keys ``website_id``, ``connector_key``, ``data_engine_url``
    — values are ``None`` when the variable is absent.
    """
    if load_dotenv_file:
        _try_load_dotenv()

    return {
        "website_id": os.environ.get(ENV_WEBSITE_ID),
        "connector_key": os.environ.get(ENV_CONNECTOR_KEY),
        "data_engine_url": os.environ.get(ENV_DATA_ENGINE_URL),
    }
