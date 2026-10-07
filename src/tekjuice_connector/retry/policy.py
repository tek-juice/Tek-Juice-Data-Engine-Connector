"""Retry policy — determines which failures are retried and when.

Design constraints
------------------
- Retries are strictly bounded (``max_attempts`` has a hard ceiling).
- Permanent failures (4xx auth errors, config errors) are NEVER retried.
  The caller (ConnectionManager) is responsible for catching those before
  calling the retry loop; the policy itself does not inspect exception types.
- The schedule is produced as a generator so the caller can consume each
  delay value one at a time and sleep between iterations.

Retryable conditions (decided by ConnectionManager, not here):
- Connection timeouts
- Network failures
- 5xx server errors

Non-retryable conditions (raised immediately by ConnectionManager):
- InvalidCredentialsError (401/403)
- WebsiteIdMismatchError
- ResponseValidationError
- ConfigurationError / ValidationError
"""

from __future__ import annotations

from typing import Callable, Generator

from tekjuice_connector.models.common import DEFAULT_BACKOFF_BASE, DEFAULT_MAX_RETRIES
from tekjuice_connector.retry.backoff import exponential_backoff

# Hard ceiling on attempts to prevent accidental infinite-like loops.
_MAX_ATTEMPTS_CEILING = 10


class RetryPolicy:
    """Configurable bounded retry policy with exponential backoff + jitter.

    Parameters
    ----------
    max_attempts:
        Total number of attempts including the first.  Capped at
        :data:`_MAX_ATTEMPTS_CEILING`.  Default: 3.
    backoff_base:
        Base delay for the exponential backoff algorithm.  Default: 1.0s.
    backoff_cap:
        Maximum ceiling before jitter is applied.  Default: 30.0s.
    random_fn:
        Random source — override in tests for determinism.
    """

    def __init__(
        self,
        max_attempts: int = DEFAULT_MAX_RETRIES,
        *,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_cap: float = 30.0,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        self._max_attempts = min(max_attempts, _MAX_ATTEMPTS_CEILING)
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._random_fn = random_fn

    @property
    def max_attempts(self) -> int:
        """Total number of attempts (including the initial one)."""
        return self._max_attempts

    def schedule(self) -> Generator[float, None, None]:
        """Yield one pre-wait delay per attempt.

        The first yielded value is always ``0.0`` (immediate).
        Subsequent values are calculated by the backoff algorithm.

        Usage::

            for attempt, delay in enumerate(policy.schedule(), start=1):
                if delay:
                    time.sleep(delay)
                try:
                    result = do_something()
                    break
                except TransientError:
                    if attempt == policy.max_attempts:
                        raise

        Yields
        ------
        float
            Seconds to wait before executing this attempt.
        """
        for i in range(self._max_attempts):
            yield exponential_backoff(
                i,
                base=self._backoff_base,
                cap=self._backoff_cap,
                random_fn=self._random_fn,
            )

    def __repr__(self) -> str:
        return (
            f"RetryPolicy("
            f"max_attempts={self._max_attempts}, "
            f"backoff_base={self._backoff_base}s, "
            f"backoff_cap={self._backoff_cap}s)"
        )
