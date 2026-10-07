"""Unit tests — retry policy and backoff algorithm."""

from __future__ import annotations

import pytest

from tekjuice_connector.retry.backoff import backoff_schedule, exponential_backoff
from tekjuice_connector.retry.policy import RetryPolicy


# ---------------------------------------------------------------------------
# exponential_backoff
# ---------------------------------------------------------------------------

class TestExponentialBackoff:
    def test_first_attempt_is_zero(self):
        assert exponential_backoff(0) == 0.0

    def test_second_attempt_positive(self):
        delay = exponential_backoff(1, base=1.0, random_fn=lambda: 1.0)
        assert delay > 0.0

    def test_respects_cap(self):
        delay = exponential_backoff(100, base=1.0, cap=5.0, random_fn=lambda: 1.0)
        assert delay <= 5.0

    def test_deterministic_with_fixed_random(self):
        d1 = exponential_backoff(2, base=1.0, random_fn=lambda: 0.5)
        d2 = exponential_backoff(2, base=1.0, random_fn=lambda: 0.5)
        assert d1 == d2

    def test_zero_random_gives_zero_delay(self):
        delay = exponential_backoff(5, random_fn=lambda: 0.0)
        assert delay == 0.0


# ---------------------------------------------------------------------------
# backoff_schedule
# ---------------------------------------------------------------------------

class TestBackoffSchedule:
    def test_length_matches_max_attempts(self):
        schedule = backoff_schedule(3)
        assert len(schedule) == 3

    def test_first_is_zero(self):
        assert backoff_schedule(3)[0] == 0.0

    def test_all_non_negative(self):
        schedule = backoff_schedule(5, base=1.0)
        assert all(d >= 0 for d in schedule)


# ---------------------------------------------------------------------------
# RetryPolicy
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_default_max_attempts(self):
        policy = RetryPolicy()
        assert policy.max_attempts == 3

    def test_custom_max_attempts(self):
        policy = RetryPolicy(max_attempts=5)
        assert policy.max_attempts == 5

    def test_max_attempts_capped_at_10(self):
        policy = RetryPolicy(max_attempts=100)
        assert policy.max_attempts == 10

    def test_zero_attempts_raises(self):
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=0)

    def test_schedule_length(self):
        policy = RetryPolicy(max_attempts=4, random_fn=lambda: 0.0)
        delays = list(policy.schedule())
        assert len(delays) == 4

    def test_first_delay_is_zero(self):
        policy = RetryPolicy(max_attempts=3, random_fn=lambda: 0.0)
        delays = list(policy.schedule())
        assert delays[0] == 0.0

    def test_subsequent_delays_non_negative(self):
        policy = RetryPolicy(max_attempts=3, random_fn=lambda: 0.5)
        delays = list(policy.schedule())
        assert all(d >= 0 for d in delays)

    def test_repr_does_not_raise(self):
        assert "RetryPolicy" in repr(RetryPolicy())
