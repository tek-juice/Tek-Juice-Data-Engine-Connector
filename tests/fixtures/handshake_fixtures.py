"""Reusable handshake request/response fixtures.

All credentials are placeholders — no real keys are present.
These fixtures mirror the confirmed Data Engine contract exactly.
"""

from __future__ import annotations

from typing import Any, Dict

# ---------------------------------------------------------------------------
# Placeholder credential values (not real)
# ---------------------------------------------------------------------------

FAKE_WEBSITE_ID = "test-website-123"
FAKE_CONNECTOR_KEY = "test-connector-key-placeholder-not-real"
FAKE_DATA_ENGINE_URL = "https://engine.example.test"

# ---------------------------------------------------------------------------
# Request fixture
# ---------------------------------------------------------------------------

HANDSHAKE_REQUEST_BODY: Dict[str, Any] = {
    "website_id": FAKE_WEBSITE_ID,
}

# ---------------------------------------------------------------------------
# Successful READY response (matches confirmed contract exactly)
# ---------------------------------------------------------------------------

HANDSHAKE_RESPONSE_READY: Dict[str, Any] = {
    "website_id": FAKE_WEBSITE_ID,
    "tenant_id": "tenant-abc-456",
    "domain": "example.com",
    "onboarding_status": "READY",
    "connector_authenticated": True,
    "backend_connected": True,
    "ready": True,
}

# ---------------------------------------------------------------------------
# Authenticated but not yet READY
# ---------------------------------------------------------------------------

HANDSHAKE_RESPONSE_PENDING: Dict[str, Any] = {
    "website_id": FAKE_WEBSITE_ID,
    "tenant_id": "tenant-abc-456",
    "domain": "example.com",
    "onboarding_status": "PENDING",
    "connector_authenticated": True,
    "backend_connected": False,
    "ready": False,
}

# ---------------------------------------------------------------------------
# Malformed responses for error-path testing
# ---------------------------------------------------------------------------

HANDSHAKE_RESPONSE_MISSING_READY: Dict[str, Any] = {
    "website_id": FAKE_WEBSITE_ID,
    "tenant_id": "tenant-abc-456",
    "domain": "example.com",
    "onboarding_status": "READY",
    "connector_authenticated": True,
    "backend_connected": True,
    # "ready" field intentionally absent
}

HANDSHAKE_RESPONSE_WRONG_WEBSITE_ID: Dict[str, Any] = {
    "website_id": "different-website-id",  # mismatch
    "tenant_id": "tenant-abc-456",
    "domain": "example.com",
    "onboarding_status": "READY",
    "connector_authenticated": True,
    "backend_connected": True,
    "ready": True,
}

HANDSHAKE_RESPONSE_NON_BOOL_FIELD: Dict[str, Any] = {
    "website_id": FAKE_WEBSITE_ID,
    "tenant_id": "tenant-abc-456",
    "domain": "example.com",
    "onboarding_status": "READY",
    "connector_authenticated": "yes",  # wrong type — should be bool
    "backend_connected": True,
    "ready": True,
}
