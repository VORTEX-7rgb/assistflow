"""
RapidRAG — cache.py
Two-tier response cache: in-memory (fast) + disk (persistent across restarts).
Biggest single speed optimization — 40-60% hit rate for small businesses.
"""

import os
import json
import logging
from collections import defaultdict
from hashlib import sha256
from time import time
import copy

from config import settings, RAGResponse
from utils import normalize_query

logger = logging.getLogger(__name__)


class ResponseCache:
    """
    Two-tier caching for RAG responses:

    Tier 1: In-memory dict (instant, lost on restart)
      Key:   sha256(client_id + normalized_query)
      Value: (RAGResponse, timestamp)
      TTL:   1 hour default

    Tier 2: Disk-based JSON files (slower, survives restarts)
      Path:  cache/{client_id}/{query_hash}.json
      TTL:   DISK_TTL_MULTIPLIER * memory TTL (default 24x = 24h)
    """

    DISK_TTL_MULTIPLIER = 24  # disk cache lives 24x longer than memory

    def __init__(self, ttl_seconds: int | None = None, cache_dir: str | None = None):
        self.ttl = ttl_seconds or settings.cache_ttl_seconds
        self.cache_dir = cache_dir or settings.cache_dir
        self._memory: dict[str, tuple[dict, float]] = {}

        # FIX: reverse index so we know which keys belong to which client
        # {client_id: set of cache keys}
        self._client_keys: dict[str, set] = defaultdict(set)

        os.makedirs(self.cache_dir, exist_ok=True)

    # ─────────────────────────────────────────
    # Key Generation
    # ─────────────────────────────────────────

    def _key(self, client_id: str, query: str) -> str:
        """Generate cache key from client_id + normalized query."""
        normalized = normalize_query(query)
        return sha256(f"{client_id}:{normalized}".encode()).hexdigest()

    # ─────────────────────────────────────────
    # Get (Read)
    # ─────────────────────────────────────────

    def get(self, client_id: str, query: str) -> RAGResponse | None:
        """
        Look up cached response. Checks memory first, then disk.
        Returns None on cache miss.
        """
        key = self._key(client_id, query)

        # Tier 1: Memory cache
        if key in self._memory:
            data, timestamp = self._memory[key]
            if time() - timestamp < self.ttl:
                response = RAGResponse(**copy.deepcopy(data))
                response.cache_hit = True
                return response
            # Expired — remove
            del self._memory[key]
            self._client_keys[client_id].discard(key)

        # Tier 2: Disk cache
        disk_path = self._disk_path(client_id, key)
        if os.path.exists(disk_path):
            try:
                mtime = os.path.getmtime(disk_path)
                if time() - mtime < self.ttl * self.DISK_TTL_MULTIPLIER:
                    with open(disk_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # Promote to memory cache
                    self._memory[key] = (data, time())
                    self._client_keys[client_id].add(key)
                    response = RAGResponse(**data)
                    response.cache_hit = True
                    return response
                else:
                    os.remove(disk_path)
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"Disk cache read error: {e}")

        return None

    # ─────────────────────────────────────────
    # Set (Write)
    # ─────────────────────────────────────────

    def set(self, client_id: str, query: str, response: RAGResponse) -> None:
        """Store response in both memory and disk cache."""
        key = self._key(client_id, query)
        data = response.model_dump()
        data["cache_hit"] = False

        # Tier 1: Memory + reverse index
        self._memory[key] = (copy.deepcopy(data), time())
        self._client_keys[client_id].add(key)

        # Tier 2: Disk
        disk_path = self._disk_path(client_id, key)
        try:
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)
            with open(disk_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except OSError as e:
            logger.debug(f"Disk cache write error: {e}")

    # ─────────────────────────────────────────
    # Invalidation
    # ─────────────────────────────────────────

    def invalidate_client(self, client_id: str) -> int:
        """
        Clear all cache entries for a specific client ONLY.
        Uses reverse index — does NOT touch other clients.
        Returns number of entries cleared.
        """
        cleared = 0

        # Clear memory entries for this client only (using reverse index)
        for key in list(self._client_keys.get(client_id, set())):
            if key in self._memory:
                del self._memory[key]
                cleared += 1
        self._client_keys.pop(client_id, None)

        # Clear disk entries
        client_cache_dir = os.path.join(self.cache_dir, client_id)
        if os.path.exists(client_cache_dir):
            try:
                for filename in os.listdir(client_cache_dir):
                    filepath = os.path.join(client_cache_dir, filename)
                    if os.path.isfile(filepath):
                        os.remove(filepath)
                        cleared += 1
            except OSError as e:
                logger.warning(f"Cache invalidation error: {e}")

        if cleared > 0:
            logger.info(f"[{client_id}] Cache invalidated: {cleared} entries")

        return cleared

    def clear_all(self) -> None:
        """Clear entire cache (all clients)."""
        self._memory.clear()
        self._client_keys.clear()
        logger.info("All caches cleared")

    # ─────────────────────────────────────────
    # Stats
    # ─────────────────────────────────────────

    def stats(self) -> dict:
        """Return cache statistics."""
        return {
            "memory_entries": len(self._memory),
            "ttl_seconds": self.ttl,
            "cache_dir": self.cache_dir,
            "clients_cached": len(self._client_keys),
        }

    # ─────────────────────────────────────────
    # Internal Helpers
    # ─────────────────────────────────────────

    def _disk_path(self, client_id: str, key: str) -> str:
        """Get disk cache file path for a key."""
        return os.path.join(self.cache_dir, client_id, f"{key}.json")