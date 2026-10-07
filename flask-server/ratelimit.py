"""
Lightweight in-process fixed-window rate limiter (Phase 4).

Keyed per client (authenticated user id when present, else client IP) and per
route bucket. A fixed 60-second window counts requests; when the count exceeds
the configured limit the caller gets HTTP 429 with a Retry-After header.

Scope + caveats
---------------
- In-process only: counters live in this worker's memory, so with multiple API
  replicas the effective limit is per-replica. For a shared, exact limit across
  replicas, back this with Redis (see docs/production-plan/02). The interface
  here (check_rate_limit) stays the same, so swapping the backend is localised.
- Disabled automatically under Flask TESTING unless a test opts in, so the
  existing suite is unaffected.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Dict, Tuple

WINDOW_SECONDS = 60

# (bucket, client_id) -> (window_start_epoch, count)
_counters: Dict[Tuple[str, str], Tuple[float, int]] = defaultdict(lambda: (0.0, 0))
_lock = threading.Lock()


def check_rate_limit(bucket: str, client_id: str, limit_per_minute: int) -> Tuple[bool, int]:
    """
    Register a request and decide whether it is allowed.

    Returns (allowed, retry_after_seconds). When not allowed, retry_after is the
    seconds remaining in the current window.
    """
    if limit_per_minute <= 0:
        return True, 0

    now = time.time()
    key = (bucket, client_id)
    with _lock:
        window_start, count = _counters[key]
        if now - window_start >= WINDOW_SECONDS:
            # New window.
            _counters[key] = (now, 1)
            return True, 0
        if count < limit_per_minute:
            _counters[key] = (window_start, count + 1)
            return True, 0
        retry_after = int(WINDOW_SECONDS - (now - window_start)) + 1
        return False, max(retry_after, 1)


def reset() -> None:
    """Clear all counters (test helper)."""
    with _lock:
        _counters.clear()
