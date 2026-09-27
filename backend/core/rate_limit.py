"""Simple in-memory fixed-window rate limiter.

Good enough for a single-process deployment. The `RateLimiter` interface is
kept small so a Redis-backed implementation can replace it for multi-worker
deployments without touching call sites.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass

from backend.core.config import get_settings
from backend.core.errors import RateLimitError


@dataclass(frozen=True)
class RateLimitRule:
    limit: int
    window_seconds: int


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    def check(self, key: str, rule: RateLimitRule) -> None:
        """Raise RateLimitError when the key exceeded the rule."""
        if not get_settings().rate_limit_enabled:
            return
        now = time.monotonic()
        with self._lock:
            window_start, count = self._buckets[key]
            if now - window_start >= rule.window_seconds:
                self._buckets[key] = (now, 1)
                return
            if count >= rule.limit:
                retry_after = int(rule.window_seconds - (now - window_start)) + 1
                raise RateLimitError(
                    f"Too many requests. Please try again in {retry_after} seconds."
                )
            self._buckets[key] = (window_start, count + 1)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# Shared limiter used by API routes (tests reset it between cases).
limiter = RateLimiter()

# Common rules
AUTH_RULE = RateLimitRule(limit=12, window_seconds=60)          # login/register/reset
UPLOAD_RULE = RateLimitRule(limit=20, window_seconds=60)        # report uploads
PROCESS_RULE = RateLimitRule(limit=30, window_seconds=60)       # process/analyze
CHAT_RULE = RateLimitRule(limit=40, window_seconds=60)          # chat messages
EXPORT_RULE = RateLimitRule(limit=15, window_seconds=60)        # PDF export
SHARE_RULE = RateLimitRule(limit=20, window_seconds=60)         # share-link management
GUEST_RULE = RateLimitRule(limit=30, window_seconds=3600)       # public guest demo per IP
