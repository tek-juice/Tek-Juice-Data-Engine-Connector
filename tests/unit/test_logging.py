"""Unit tests — log sanitization."""

from __future__ import annotations

import logging

import pytest

from tekjuice_connector.logging.sanitization import (
    SanitizingFilter,
    sanitize_message,
)
from tekjuice_connector.security.redaction import REDACTED_MARKER


class TestSanitizingFilter:
    def _make_record(self, msg: str, args=()) -> logging.LogRecord:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=args,
            exc_info=None,
        )
        return record

    def test_long_token_in_msg_is_redacted(self):
        token = "sk-abcdef1234567890abcdef"
        record = self._make_record(f"Connecting with key={token}")
        f = SanitizingFilter()
        f.filter(record)
        assert token not in record.msg

    def test_normal_message_preserved(self):
        record = self._make_record("Connection established.")
        f = SanitizingFilter()
        f.filter(record)
        assert "Connection established." in record.msg

    def test_args_tuple_sanitized(self):
        token = "sk-abcdef1234567890abcdef"
        record = self._make_record("key=%s", args=(token,))
        f = SanitizingFilter()
        f.filter(record)
        assert token not in str(record.args)

    def test_sensitive_extra_attr_redacted(self):
        record = self._make_record("msg")
        record.connector_key = "sk-abcdef1234567890abcdef"
        f = SanitizingFilter()
        f.filter(record)
        assert record.connector_key == REDACTED_MARKER

    def test_filter_returns_true(self):
        record = self._make_record("test")
        f = SanitizingFilter()
        assert f.filter(record) is True


class TestSanitizeMessage:
    def test_long_token_redacted(self):
        token = "sk-abcdef1234567890abcdef"
        result = sanitize_message(f"Using key {token}")
        assert token not in result

    def test_safe_message_unchanged(self):
        msg = "Connection attempt 1 of 3"
        assert sanitize_message(msg) == msg
