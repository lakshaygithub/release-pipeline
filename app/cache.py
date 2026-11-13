import json
import logging
from typing import Any

import redis

log = logging.getLogger(__name__)


class ItemCache:
    """Read-through cache for items.

    Redis is treated as optional: if it is down, every call degrades to a
    cache miss and the service keeps answering from the database.
    """

    def __init__(self, client: "redis.Redis | None", ttl_seconds: int, enabled: bool = True):
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.enabled = enabled and client is not None

    @staticmethod
    def _key(item_id: int) -> str:
        return f"item:{item_id}"

    def get(self, item_id: int) -> "dict[str, Any] | None":
        if not self.enabled:
            return None
        try:
            raw = self.client.get(self._key(item_id))
        except redis.RedisError as exc:
            log.warning("cache get failed, falling back to database: %s", exc)
            return None
        return json.loads(raw) if raw else None

    def set(self, item_id: int, value: "dict[str, Any]") -> None:
        if not self.enabled:
            return
        try:
            self.client.set(self._key(item_id), json.dumps(value), ex=self.ttl_seconds)
        except redis.RedisError as exc:
            log.warning("cache set failed: %s", exc)

    def invalidate(self, item_id: int) -> None:
        if not self.enabled:
            return
        try:
            self.client.delete(self._key(item_id))
        except redis.RedisError as exc:
            # A stale entry expires after ttl_seconds at worst.
            log.warning("cache invalidate failed: %s", exc)

    def ping(self) -> bool:
        if not self.enabled:
            return True
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False
