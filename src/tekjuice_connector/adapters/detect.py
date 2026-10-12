"""Best-effort detection of the host web framework (informational only)."""

from __future__ import annotations

import sys
from typing import Optional

# Most specific first: FastAPI is built on Starlette.
_FRAMEWORKS = ("fastapi", "starlette", "django", "flask")


def detect_framework() -> Optional[str]:
    """Return the first known framework already imported by the process.

    Reported to the Data Engine for diagnostics only; nothing depends on it.
    """
    for name in _FRAMEWORKS:
        if name in sys.modules:
            return name
    return None
