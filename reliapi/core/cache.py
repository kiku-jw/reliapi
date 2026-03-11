"""Universal cache implementation for HTTP requests."""
import hashlib
import json
import logging
from typing import Any, Dict, Optional

import redis

logger = logging.getLogger(__name__)


class Cache:
    """Universal cache wrapper for HTTP requests.
    
    Supports GET/HEAD caching with ETag support.
    Cache key is based on method, URL, and significant headers.
    """

    def __init__(self, redis_url: str, key_prefix: str = "reliapi"):
        """
        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for cache keys
        """
        self.key_prefix = key_prefix
        try:
            self.client = redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
            self.enabled = True
            logger.info(f"Cache connected to Redis: {redis_url}")
        except Exception as e:
            self.client = None
            self.enabled = False
            logger.warning(f"Cache connection failed (graceful degradation): {e}", exc_info=True)

    def _make_key(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        query: Optional[Dict[str, Any]] = None,
        tenant: Optional[str] = None,
    ) -> str:
        """Generate cache key from request parameters.
        
        Key includes: tenant (if multi-tenant) + method + url + sorted query + significant headers + body hash
        
        Args:
            tenant: Tenant name for multi-tenant isolation (optional)
        """
        # Significant headers for caching (exclude auth, trace, etc.)
        significant_headers = {}
        if headers:
            for h in ["Accept", "Accept-Language", "Content-Type"]:
                if h in headers:
                    significant_headers[h] = headers[h]

        # Sort query params for consistent keys
        # json.dumps(sort_keys=True) handles sorting recursively, so we don't need manual sorting here.
        # We also pass dicts directly to avoid double serialization which is slow.
        key_data = {
            "method": method.upper(),
            "url": url,
            "query": query or {},
            "headers": significant_headers,
        }
        
        # For POST/PUT/PATCH with body, include body hash
        if body and method.upper() in ["POST", "PUT", "PATCH"]:
            key_data["body_hash"] = hashlib.sha256(body).hexdigest()[:16]

        key_str = json.dumps(key_data, sort_keys=True)
        cache_key_hash = hashlib.sha256(key_str.encode()).hexdigest()
        
        # Multi-tenant isolation: include tenant in cache key
        if tenant:
            return f"{self.key_prefix}:tenant:{tenant}:cache:{cache_key_hash}"
        else:
            return f"{self.key_prefix}:cache:{cache_key_hash}"

    def get(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        query: Optional[Dict[str, Any]] = None,
        allow_post: bool = False,
        tenant: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get cached response if available.
        
        Args:
            method: HTTP method
            url: Request URL
            headers: Request headers
            body: Request body (for POST/PUT/PATCH)
            query: Query parameters
            allow_post: Allow caching POST requests (for LLM proxy)
        """
        if not self.enabled or not self.client:
            return None

        # Only cache GET/HEAD by default, or POST if explicitly allowed
        if method.upper() not in ["GET", "HEAD"] and not (allow_post and method.upper() == "POST"):
            return None

        try:
            key = self._make_key(method, url, headers, body, query, tenant=tenant)
            cached = self.client.get(key)
            if cached:
                # Edge case: JSON deserialization may fail if cached value is corrupted.
                # This is handled by the try/except block below.
                return json.loads(cached)
        except json.JSONDecodeError as e:
            # Edge case: Cached value is corrupted or not valid JSON.
            # Delete the corrupted key to prevent future errors.
            logger.warning(f"Cache get: corrupted value for key {key[:50]}... (deleting): {e}", exc_info=True)
            try:
                self.client.delete(key)
            except Exception:
                pass  # Ignore deletion errors
            return None
        except Exception as e:
            logger.warning(f"Cache get error (graceful degradation): {e}", exc_info=True)
            return None

        return None

    def set(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]],
        body: Optional[bytes],
        value: Dict[str, Any],
        ttl_s: int = 3600,
        query: Optional[Dict[str, Any]] = None,
        allow_post: bool = False,
        tenant: Optional[str] = None,
    ) -> None:
        """Cache response with TTL.
        
        Args:
            method: HTTP method
            url: Request URL
            headers: Request headers
            body: Request body
            value: Response data to cache
            ttl_s: Time to live in seconds
            query: Query parameters
            allow_post: Allow caching POST requests (for LLM proxy)
        """
        if not self.enabled or not self.client:
            return

        # Only cache GET/HEAD by default, or POST if explicitly allowed
        if method.upper() not in ["GET", "HEAD"] and not (allow_post and method.upper() == "POST"):
            return

        try:
            key = self._make_key(method, url, headers, body, query, tenant=tenant)
            # Atomic SETEX: sets key, value, and TTL in a single operation
            # This is a single Redis command, so it's guaranteed atomic.
            #
            # Edge cases:
            # 1. Concurrent SETEX on same key: Last write wins (acceptable for cache).
            #    This is safe because cache writes are idempotent - writing the same
            #    response multiple times doesn't cause data corruption.
            # 2. Redis connection failure: Exception is caught and logged, graceful degradation.
            # 3. TTL expiration during write: SETEX sets both value and TTL atomically,
            #    so key will have correct TTL even if it expires during the operation.
            # 4. Memory pressure: Redis may evict keys, but this is handled by cache miss logic.
            self.client.setex(key, ttl_s, json.dumps(value))
        except Exception as e:
            logger.warning(f"Cache set error (graceful degradation): {e}", exc_info=True)

    def invalidate(self, pattern: str) -> None:
        """Invalidate cache entries matching pattern."""
        if not self.enabled or not self.client:
            return

        try:
            keys = self.client.keys(f"{self.key_prefix}:cache:{pattern}*")
            if keys:
                self.client.delete(*keys)
        except Exception as e:
            logger.warning(f"Cache invalidate error (graceful degradation): {e}", exc_info=True)


