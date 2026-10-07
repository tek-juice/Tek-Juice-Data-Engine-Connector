"""Unit tests — security: redaction, SecretStr, URL safety."""

from __future__ import annotations

import pytest

from tekjuice_connector.security.redaction import (
    REDACTED_MARKER,
    redact_dict,
    redact_env_value,
    redact_header_value,
    redact_headers,
    redact_string,
    safe_url,
)
from tekjuice_connector.security.secrets import SecretStr
from tekjuice_connector.security.validation import (
    assert_https,
    assert_no_credentials_in_url,
    validate_url_security,
)
from tekjuice_connector.exceptions.validation import ValidationError
from tekjuice_connector.models.credentials import ConnectorCredentials


# ---------------------------------------------------------------------------
# Header redaction
# ---------------------------------------------------------------------------

class TestRedactHeaderValue:
    def test_connector_key_header_redacted(self):
        result = redact_header_value("X-Tek-Juice-Connector-Key", "secret-value")
        assert result == REDACTED_MARKER

    def test_case_insensitive(self):
        result = redact_header_value("x-tek-juice-connector-key", "secret")
        assert result == REDACTED_MARKER

    def test_authorization_redacted(self):
        result = redact_header_value("Authorization", "Bearer token123")
        assert result == REDACTED_MARKER

    def test_non_sensitive_header_preserved(self):
        result = redact_header_value("Content-Type", "application/json")
        assert result == "application/json"

    def test_redact_headers_dict(self):
        headers = {
            "X-Tek-Juice-Connector-Key": "my-secret",
            "Content-Type": "application/json",
        }
        safe = redact_headers(headers)
        assert safe["X-Tek-Juice-Connector-Key"] == REDACTED_MARKER
        assert safe["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Env value redaction
# ---------------------------------------------------------------------------

class TestRedactEnvValue:
    def test_connector_key_redacted(self):
        assert redact_env_value("tekjuice_connector_key", "secret") == REDACTED_MARKER

    def test_password_redacted(self):
        assert redact_env_value("DB_PASSWORD", "hunter2") == REDACTED_MARKER

    def test_non_sensitive_preserved(self):
        assert redact_env_value("WEBSITE_ID", "my-site") == "my-site"

    def test_dict_redaction(self):
        data = {"connector_key": "secret", "website_id": "site-1"}
        safe = redact_dict(data)
        assert safe["connector_key"] == REDACTED_MARKER
        assert safe["website_id"] == "site-1"


# ---------------------------------------------------------------------------
# String redaction
# ---------------------------------------------------------------------------

class TestRedactString:
    def test_long_token_redacted(self):
        token = "sk-abcdef1234567890abcdef"
        result = redact_string(f"key={token}")
        assert token not in result

    def test_short_word_preserved(self):
        result = redact_string("hello world")
        assert result == "hello world"

    def test_empty_string(self):
        assert redact_string("") == ""


# ---------------------------------------------------------------------------
# safe_url
# ---------------------------------------------------------------------------

class TestSafeUrl:
    def test_normal_url_unchanged(self):
        url = "https://engine.example.com/api/v1"
        assert safe_url(url) == url

    def test_embedded_credentials_removed(self):
        url = "https://user:password@engine.example.com"
        result = safe_url(url)
        assert "password" not in result
        assert "user" not in result
        assert "engine.example.com" in result


# ---------------------------------------------------------------------------
# SecretStr
# ---------------------------------------------------------------------------

class TestSecretStr:
    def test_repr_redacted(self):
        s = SecretStr("my-secret-key-value")
        assert "my-secret-key-value" not in repr(s)
        assert REDACTED_MARKER in repr(s)

    def test_str_redacted(self):
        s = SecretStr("my-secret")
        assert "my-secret" not in str(s)

    def test_format_redacted(self):
        s = SecretStr("my-secret")
        assert "my-secret" not in f"key={s}"

    def test_reveal_returns_value(self):
        s = SecretStr("actual-value")
        assert s.reveal() == "actual-value"

    def test_hint_shows_prefix(self):
        s = SecretStr("sk-abc123")
        assert s.hint.startswith("sk-a")
        assert "..." in s.hint

    def test_equality(self):
        assert SecretStr("abc") == SecretStr("abc")
        assert SecretStr("abc") != SecretStr("xyz")


# ---------------------------------------------------------------------------
# ConnectorCredentials repr safety
# ---------------------------------------------------------------------------

class TestCredentialsRepr:
    def test_key_not_in_repr(self):
        creds = ConnectorCredentials(
            website_id="my-site",
            connector_key="super-secret-key-that-must-not-appear",
            data_engine_url="https://engine.example.test",
        )
        assert "super-secret-key-that-must-not-appear" not in repr(creds)
        assert "super-secret-key-that-must-not-appear" not in str(creds)

    def test_website_id_in_repr(self):
        creds = ConnectorCredentials(
            website_id="my-site",
            connector_key="key",
            data_engine_url="https://engine.example.test",
        )
        assert "my-site" in repr(creds)

    def test_safe_key_hint(self):
        creds = ConnectorCredentials(
            website_id="site",
            connector_key="sk-abc-full-key",
            data_engine_url="https://engine.test",
        )
        hint = creds.safe_key_hint
        assert hint.startswith("sk-a")
        assert "full-key" not in hint


# ---------------------------------------------------------------------------
# URL security validation
# ---------------------------------------------------------------------------

class TestUrlSecurity:
    def test_https_passes(self):
        assert_https("https://engine.example.com")  # must not raise

    def test_http_raises_by_default(self):
        with pytest.raises(ValidationError):
            assert_https("http://engine.example.com", allow_http=False)

    def test_http_with_allow_warns(self, recwarn):
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            assert_https("http://engine.example.com", allow_http=True)
        assert any("HTTP" in str(warning.message) for warning in w)

    def test_embedded_credentials_rejected(self):
        with pytest.raises(ValidationError):
            assert_no_credentials_in_url("https://user:pass@engine.example.com")

    def test_clean_url_passes(self):
        assert_no_credentials_in_url("https://engine.example.com")  # must not raise
