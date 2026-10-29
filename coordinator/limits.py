"""Bounded process-local request windows; account/auth security limits stay durable."""

from threading import Lock
import time

from .auth import AuthError


class RequestLimits:
    def __init__(self, *, monotonic=time.monotonic, max_buckets=10000):
        self.monotonic, self.max_buckets = monotonic, max_buckets
        self.buckets = {}
        self.lock = Lock()

    def consume(self, key, limit, window_s=60):
        now = self.monotonic()
        with self.lock:
            value = self.buckets.get(key)
            if value is None or now >= value[1]:
                if len(self.buckets) >= self.max_buckets:
                    self.buckets = {k: v for k, v in self.buckets.items() if v[1] > now}
                if len(self.buckets) >= self.max_buckets and key not in self.buckets:
                    raise AuthError(503, "service_busy", retry_after=1)
                self.buckets[key] = (1, now + window_s)
                return
            count, end = value
            if count >= limit:
                raise AuthError(429, "rate_limited", retry_after=max(1, int(end - now) + 1))
            self.buckets[key] = (count + 1, end)
