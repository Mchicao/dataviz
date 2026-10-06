"""Thread-safe rate limiting boundary for the DataVIZ SaaS service.

Provides sliding window token bucket rate limiting with per-tenant and per-principal
isolation, burst caps, and configurable refill rates.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import RLock

from apps.dataviz_service.contracts import SanitizedServiceError

logger = logging.getLogger("dataviz_service.security.rate_limit")


class RateLimitExceededError(SanitizedServiceError):
    """Raised when request rate exceeds configured tenant or principal limits."""

    def __init__(self, message: str = "Rate limit exceeded. Please retry later.") -> None:
        super().__init__(message, status_code=429, error_code="RATE_LIMIT_EXCEEDED")


class _TokenBucket:
    def __init__(self, capacity: float, refill_rate: float, now: float) -> None:
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self.last_update = now

    def consume(self, tokens: float, now: float) -> bool:
        elapsed = max(0.0, now - self.last_update)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_update = now

        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """Thread-safe token-bucket rate limiter with tenant and principal scope isolation."""

    def __init__(
        self,
        *,
        default_requests_per_minute: float = 60.0,
        default_burst_capacity: float = 10.0,
    ) -> None:
        if default_requests_per_minute <= 0 or default_burst_capacity <= 0:
            raise ValueError("Rate limit parameters must be positive numbers.")
        self._rate = default_requests_per_minute / 60.0
        self._capacity = default_burst_capacity
        self._buckets: dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(self._capacity, self._rate, time.monotonic())
        )
        self._lock = RLock()

    def check_rate_limit(self, scope_key: str, tokens: float = 1.0) -> None:
        """Check and consume rate limit tokens for a given tenant or principal key.

        Raises :class:`RateLimitExceededError` if the bucket is exhausted.
        """
        if not scope_key or not scope_key.strip():
            raise RateLimitExceededError("Scope key must not be empty.")

        key = scope_key.strip()
        now = time.monotonic()

        with self._lock:
            bucket = self._buckets[key]
            if not bucket.consume(tokens, now):
                logger.warning("Rate limit boundary: limit exceeded for key '%s'", key)
                raise RateLimitExceededError(f"Rate limit exceeded for key '{key}'.")

    def reset(self) -> None:
        """Clear all rate limiting buckets (useful for tests)."""
        with self._lock:
            self._buckets.clear()
