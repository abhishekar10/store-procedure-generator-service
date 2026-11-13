"""A small in-process token-bucket rate limiter for the generation endpoints.

This is a fallback, not the primary control. Each process keeps its own
buckets, so with N tasks behind a load balancer a client gets up to N times
the configured rate, and every restart forgets them. The real per-client
limit belongs in front of the service: AWS WAF rate-based rules on the ALB,
or an API gateway's usage plans. This exists so a single task cannot be
driven into the ground by one caller even when nothing sits in front of it.

Generation is the expensive path (each request is minutes of model time), so
only it is limited; /health, /ready and /schema are not.
"""

import math
import threading
import time
from dataclasses import dataclass

# Above this many tracked clients, buckets that have refilled completely are
# dropped -- they carry no state a fresh bucket would not.
_PRUNE_THRESHOLD = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(self, rate_per_minute: float, burst: int, clock=time.monotonic):
        self.rate_per_second = rate_per_minute / 60.0
        self.burst = burst
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def _refill(self, bucket: _Bucket, now: float) -> None:
        bucket.tokens = min(self.burst, bucket.tokens + (now - bucket.updated) * self.rate_per_second)
        bucket.updated = now

    def acquire(self, key: str) -> float | None:
        """Takes one token for `key`. Returns None if allowed, otherwise the
        number of seconds until a token will be available."""
        with self._lock:
            now = self._clock()
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= _PRUNE_THRESHOLD:
                    self._prune(now)
                bucket = self._buckets[key] = _Bucket(tokens=self.burst, updated=now)
            else:
                self._refill(bucket, now)

            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return None
            return math.ceil((1 - bucket.tokens) / self.rate_per_second)

    def _prune(self, now: float) -> None:
        for key in list(self._buckets):
            bucket = self._buckets[key]
            self._refill(bucket, now)
            if bucket.tokens >= self.burst:
                del self._buckets[key]
