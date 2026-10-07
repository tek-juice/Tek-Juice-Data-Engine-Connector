"""Exponential backoff with full jitter.

Algorithm
---------
delay = min(cap, base * 2^attempt) * random(0, 1)

This is the "Full Jitter" strategy from the AWS Architecture Blog post
"Exponential Backoff And Jitter" which produces the best throughput
characteristics under contention.

All functions are pure (accept a ``random_fn`` parameter) so they can be
tested deterministically by passing a fixed-value callable.
"""

from __future__ import annotations

import random as _random
from typing import Callable


def exponential_backoff(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    random_fn: Callable[[], float] | None = None,
) -> float:
    """Return the delay in seconds for *attempt* (0-indexed).

    Parameters
    ----------
    attempt:
        Zero-based attempt index.  The first attempt (index 0) returns 0
        so the initial try is always immediate.
    base:
        Base delay in seconds.  Default ``1.0``.
    cap:
        Maximum delay in seconds before jitter.  Default ``30.0``.
    random_fn:
        Callable returning a float in [0, 1).  Defaults to
        ``random.random``.  Override in tests for determinism.

    Returns
    -------
    float
        Seconds to wait before the next attempt.  Always ≥ 0.
    """
    if attempt <= 0:
        return 0.0

    rand = random_fn if random_fn is not None else _random.random
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    return rand() * ceiling


def backoff_schedule(
    max_attempts: int,
    *,
    base: float = 1.0,
    cap: float = 30.0,
    random_fn: Callable[[], float] | None = None,
) -> list[float]:
    """Return the full delay schedule as a list for *max_attempts* attempts.

    The first entry is always ``0.0`` (immediate first try).  There are
    exactly *max_attempts* entries, matching the number of calls to make.

    Parameters
    ----------
    max_attempts:
        Total number of attempts including the initial one.
    base:
        Backoff base in seconds.
    cap:
        Maximum ceiling before jitter in seconds.
    random_fn:
        Random source — override in tests.

    Returns
    -------
    list[float]
    """
    return [
        exponential_backoff(i, base=base, cap=cap, random_fn=random_fn)
        for i in range(max_attempts)
    ]
