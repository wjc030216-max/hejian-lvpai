"""Sliding-window limiter backed by Redis, with an in-process fallback."""
from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self):
        self._events = defaultdict(deque)
        self._mutex = threading.Lock()
        self._last_cleanup = 0.0
        self._redis = None
        self._redis_checked = False

    def _client(self):
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        url = (os.getenv('REDIS_URL') or '').strip()
        if not url:
            return None
        try:
            import redis
            client = redis.from_url(url, decode_responses=True,
                                    socket_timeout=1, socket_connect_timeout=1)
            client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        limit = max(1, int(limit))
        window = max(1, int(window_seconds))
        client = self._client()
        if client is not None:
            try:
                now = time.time()
                redis_key = 'rate:' + key
                member = f'{now}:{uuid.uuid4().hex[:8]}'
                pipe = client.pipeline()
                pipe.zremrangebyscore(redis_key, 0, now - window)
                pipe.zadd(redis_key, {member: now})
                pipe.zcard(redis_key)
                pipe.expire(redis_key, window + 5)
                _, _, count, _ = pipe.execute()
                return int(count) <= limit
            except Exception:
                # Redis hiccup: keep serving with the local limiter.
                self._redis = None
        return self._allow_local(key, limit, window)

    def _allow_local(self, key, limit, window):
        now = time.monotonic()
        cutoff = now - window
        with self._mutex:
            bucket = self._events[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            allowed = len(bucket) < limit
            if allowed:
                bucket.append(now)
            if now - self._last_cleanup > 300:
                stale = [name for name, values in self._events.items()
                         if not values or values[-1] < cutoff]
                for name in stale:
                    self._events.pop(name, None)
                self._last_cleanup = now
            return allowed
