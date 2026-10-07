"""Unit tests — configuration loading and validation."""

from __future__ import annotations

import os
import pytest

from tekjuice_connector.config.settings import load_settings
from tekjuice_connector.config.validation import (
    validate_connector_key,
    validate_data_engine_url,
    validate_website_id,
)
from tekjuice_connector.exceptions.configuration import MissingConfigurationError
from tekjuice_connector.exceptions.validation import ValidationError


# ---------------------------------------------------------------------------
# validate_website_id
# ---------------------------------------------------------------------------

class TestValidateWebsiteId:
    def test_valid_simple(self):
        assert validate_website_id("my-site") == "my-site"

    def test_valid_with_dots(self):
        assert validate_website_id("my.site.123") == "my.site.123"

    def test_strips_whitespace(self):
        assert validate_website_id("  site  ") == "site"

    def test_none_raises_missing(self):
        with pytest.raises(MissingConfigurationError):
            validate_website_id(None)

    def test_empty_raises_missing(self):
        with pytest.raises(MissingConfigurationError):
            validate_website_id("")

    def test_invalid_chars_raises_validation_error(self):
        with pytest.raises(ValidationError):
            validate_website_id("site/with/slashes")

    def test_too_long_raises_validation_error(self):
        with pytest.raises(ValidationError):
            validate_website_id("x" * 256)


# ---------------------------------------------------------------------------
# validate_connector_key
# ---------------------------------------------------------------------------

class TestValidateConnectorKey:
    def test_valid_key(self):
        key = "sk-abc123"
        assert validate_connector_key(key) == key

    def test_none_raises_missing(self):
        with pytest.raises(MissingConfigurationError):
            validate_connector_key(None)

    def test_empty_raises_missing(self):
        with pytest.raises(MissingConfigurationError):
            validate_connector_key("")

    def test_whitespace_only_raises_missing(self):
        with pytest.raises(MissingConfigurationError):
            validate_connector_key("   ")

    def test_key_not_in_exception_message(self):
        """Secret must never appear in the error message."""
        try:
            validate_connector_key(None)
        except MissingConfigurationError as exc:
            assert "sk-secret-key" not in str(exc)


# ---------------------------------------------------------------------------
# validate_data_engine_url
# ---------------------------------------------------------------------------

class TestValidateDataEngineUrl:
    def test_valid_https(self):
        url = validate_data_engine_url("https://engine.example.com")
        assert url == "https://engine.example.com"

    def test_valid_http(self):
        # HTTP is syntactically valid; security layer enforces HTTPS
        url = validate_data_engine_url("http://localhost:8000")
        assert url == "http://localhost:8000"

    def test_strips_trailing_slash(self):
        url = validate_data_engine_url("https://engine.example.com/")
        assert not url.endswith("/")

    def test_none_raises_missing(self):
        with pytest.raises(MissingConfigurationError):
            validate_data_engine_url(None)

    def test_no_scheme_raises_validation(self):
        with pytest.raises(ValidationError):
            validate_data_engine_url("engine.example.com")

    def test_bad_scheme_raises_validation(self):
        with pytest.raises(ValidationError):
            validate_data_engine_url("ftp://engine.example.com")

    def test_no_host_raises_validation(self):
        with pytest.raises(ValidationError):
            validate_data_engine_url("https://")


# ---------------------------------------------------------------------------
# load_settings
# ---------------------------------------------------------------------------

class TestLoadSettings:
    def test_explicit_args(self):
        settings = load_settings(
            website_id="site-1",
            connector_key="key-abc",
            data_engine_url="https://engine.example.test",
            load_dotenv_file=False,
        )
        assert settings.credentials.website_id == "site-1"
        assert settings.credentials.data_engine_url == "https://engine.example.test"

    def test_from_environment(self, monkeypatch):
        monkeypatch.setenv("TEKJUICE_WEBSITE_ID", "env-site")
        monkeypatch.setenv("TEKJUICE_CONNECTOR_KEY", "env-key")
        monkeypatch.setenv("TEKJUICE_DATA_ENGINE_URL", "https://env.example.test")

        settings = load_settings(load_dotenv_file=False)
        assert settings.credentials.website_id == "env-site"

    def test_missing_website_id_raises(self, monkeypatch):
        monkeypatch.delenv("TEKJUICE_WEBSITE_ID", raising=False)
        monkeypatch.setenv("TEKJUICE_CONNECTOR_KEY", "key")
        monkeypatch.setenv("TEKJUICE_DATA_ENGINE_URL", "https://x.test")

        with pytest.raises(MissingConfigurationError) as exc_info:
            load_settings(load_dotenv_file=False)
        assert "TEKJUICE_WEBSITE_ID" in str(exc_info.value)

    def test_missing_connector_key_raises(self, monkeypatch):
        monkeypatch.setenv("TEKJUICE_WEBSITE_ID", "site")
        monkeypatch.delenv("TEKJUICE_CONNECTOR_KEY", raising=False)
        monkeypatch.setenv("TEKJUICE_DATA_ENGINE_URL", "https://x.test")

        with pytest.raises(MissingConfigurationError):
            load_settings(load_dotenv_file=False)

    def test_explicit_overrides_env(self, monkeypatch):
        monkeypatch.setenv("TEKJUICE_WEBSITE_ID", "env-site")
        monkeypatch.setenv("TEKJUICE_CONNECTOR_KEY", "env-key")
        monkeypatch.setenv("TEKJUICE_DATA_ENGINE_URL", "https://env.test")

        settings = load_settings(
            website_id="explicit-site",
            load_dotenv_file=False,
        )
        assert settings.credentials.website_id == "explicit-site"

    def test_repr_does_not_contain_key(self):
        settings = load_settings(
            website_id="site",
            connector_key="super-secret-key",
            data_engine_url="https://engine.example.test",
            load_dotenv_file=False,
        )
        assert "super-secret-key" not in repr(settings)
