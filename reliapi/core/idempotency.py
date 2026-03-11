"""Universal idempotency management for HTTP requests."""
import hashlib
import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

import redis

logger = logging.getLogger(__name__)


class IdempotencyManager:
    """Manages idempotency keys and request coalescing for any HTTP method.
    
    Supports POST/PUT/PATCH with Idempotency-Key header.
    """

    def __init__(self, redis_url: str, key_prefix: str = "reliapi"):
        """
        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for idempotency keys
        """
        self.key_prefix = key_prefix
        try:
            self.client = redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
            self.enabled = True
            logger.info(f"Idempotency connected to Redis: {redis_url}")
        except Exception as e:
            self.client = None
            self.enabled = False
            logger.warning(f"Idempotency connection failed (graceful degradation): {e}", exc_info=True)

    def make_request_hash(
        self,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
    ) -> str:
        """Generate hash of request for comparison."""
        key_data = {
            "method": method.upper(),
            "url": url,
            "headers": json.dumps(headers or {}, sort_keys=True),
        }
        if body:
            key_data["body_hash"] = hashlib.sha256(body).hexdigest()
        
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()

    def register_request(
        self,
        idempotency_key: str,
        method: str,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        request_id: Optional[str] = None,
        tenant: Optional[str] = None,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Register idempotency key atomically using Redis SETNX.
        
        Returns:
            (is_new, existing_request_id, existing_request_hash)
            - is_new: True if this is a new request
            - existing_request_id: Request ID if already exists
            - existing_request_hash: Hash of existing request body
        """
        if not self.enabled or not self.client:
            return True, None, None

        request_hash = self.make_request_hash(method, url, headers, body)
        # Multi-tenant isolation: include tenant in idempotency key
        if tenant:
            key = f"{self.key_prefix}:tenant:{tenant}:idempotency:{idempotency_key}"
        else:
            key = f"{self.key_prefix}:idempotency:{idempotency_key}"

        try:
            # First, try to get existing key
            existing = self.client.get(key)
            if existing:
                data = json.loads(existing)
                existing_hash = data.get("request_hash")
                existing_request_id = data.get("request_id")

                # If request body differs, return conflict
                if existing_hash != request_hash:
                    return False, existing_request_id, existing_hash

                # Same request, return existing
                return False, existing_request_id, existing_hash

            # New request: use SET with nx=True and ex=... to atomically register with TTL
            # This prevents race conditions when multiple requests arrive simultaneously.
            #
            # Edge cases:
            # 1. Concurrent registration: If two requests arrive at the same time with same key,
            #    only one will succeed (was_set=True). The other will get was_set=False and
            #    can then read the existing data. This is the core coalescing behavior.
            # 2. TTL expiration: The ex=3600 parameter sets TTL atomically, preventing key
            #    from existing without expiration. This ensures keys don't leak memory.
            # 3. Key deletion race: If key is deleted between SET (nx=True) and GET, we treat
            #    it as a new request (return True). This is rare but handled gracefully.
            # 4. Redis connection failure: Exception is caught, graceful degradation (return True).
            # 5. Request body mismatch: If same key but different body hash, return conflict
            #    (is_new=False, different hash) to prevent idempotency abuse.
            data = {
                "request_id": request_id or f"req_{int(time.time())}_{hashlib.md5(idempotency_key.encode()).hexdigest()[:8]}",
                "request_hash": request_hash,
                "created_at": time.time(),
            }
            data_json = json.dumps(data)
            
            # Atomic SET with NX (only if not exists) and EX (expiration)
            # This is a single Redis command, so it's guaranteed atomic.
            # NX ensures only one request can register, EX sets TTL atomically.
            was_set = self.client.set(key, data_json, nx=True, ex=3600)
            
            if was_set:
                # Successfully registered new request
                # This request will proceed to upstream, others will wait for result
                return True, None, None
            else:
                # Another request registered it first, get the existing data
                # This request will wait for the first request to complete
                existing = self.client.get(key)
                if existing:
                    existing_data = json.loads(existing)
                    existing_hash = existing_data.get("request_hash")
                    existing_request_id = existing_data.get("request_id")
                    
                    # Check if request body matches
                    # If hash differs, it's a conflict (same key, different request)
                    if existing_hash != request_hash:
                        return False, existing_request_id, existing_hash
                    
                    # Same request body, return existing request ID for coalescing
                    return False, existing_request_id, existing_hash
                
                # Edge case: key was deleted between SET (nx=True) and GET
                # This is extremely rare (key expired or manually deleted), treat as new request
                return True, None, None
                
        except Exception as e:
            logger.warning(f"Idempotency register_request error (graceful degradation): {e}", exc_info=True)
            return True, None, None

    def get_result(self, idempotency_key: str, tenant: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get cached result for idempotency key.
        
        Edge cases:
        - If result is corrupted JSON, returns None (treats as cache miss)
        - If Redis is unavailable, returns None (graceful degradation)
        - If key expired, returns None (normal behavior)
        """
        if not self.enabled or not self.client:
            return None

        # Multi-tenant isolation: include tenant in result key
        if tenant:
            result_key = f"{self.key_prefix}:tenant:{tenant}:idempotency_result:{idempotency_key}"
        else:
            result_key = f"{self.key_prefix}:idempotency_result:{idempotency_key}"

        try:
            result = self.client.get(result_key)
            if result:
                # Edge case: JSON deserialization may fail if cached value is corrupted.
                # This is handled by the try/except block below.
                return json.loads(result)
        except json.JSONDecodeError as e:
            # Edge case: Cached result is corrupted or not valid JSON.
            # Delete the corrupted key to prevent future errors.
            logger.warning(f"Idempotency get_result: corrupted value for key {result_key[:50]}... (deleting): {e}", exc_info=True)
            try:
                self.client.delete(result_key)
            except Exception:
                pass  # Ignore deletion errors
            return None
        except Exception as e:
            logger.warning(f"Idempotency get_result error (graceful degradation): {e}", exc_info=True)
            return None

        return None

    def store_result(
        self, idempotency_key: str, result: Dict[str, Any], ttl_s: int = 3600, tenant: Optional[str] = None
    ) -> None:
        """Store result for idempotency key.
        
        Edge cases:
        - If JSON serialization fails, result is not stored (request continues normally)
        - If Redis is unavailable, result is not stored (graceful degradation)
        - Concurrent stores: Last write wins (acceptable for idempotency results)
        - TTL expiration: SETEX sets TTL atomically, preventing memory leaks
        """
        if not self.enabled or not self.client:
            return

        # Multi-tenant isolation: include tenant in result key
        if tenant:
            result_key = f"{self.key_prefix}:tenant:{tenant}:idempotency_result:{idempotency_key}"
        else:
            result_key = f"{self.key_prefix}:idempotency_result:{idempotency_key}"
        try:
            # Atomic SETEX: sets key, value, and TTL in a single operation
            # This prevents race conditions where key exists without TTL.
            self.client.setex(result_key, ttl_s, json.dumps(result))
        except (TypeError, ValueError) as e:
            # Edge case: Result cannot be serialized to JSON (e.g., contains non-serializable objects)
            logger.warning(f"Idempotency store_result: cannot serialize result: {e}", exc_info=True)
        except Exception as e:
            logger.warning(f"Idempotency store_result error (graceful degradation): {e}", exc_info=True)

    def is_in_progress(self, idempotency_key: str, tenant: Optional[str] = None) -> bool:
        """Check if request with this key is in progress."""
        if not self.enabled or not self.client:
            return False

        # Multi-tenant isolation: include tenant in in-progress key
        if tenant:
            key = f"{self.key_prefix}:tenant:{tenant}:idempotency_in_progress:{idempotency_key}"
        else:
            key = f"{self.key_prefix}:idempotency_in_progress:{idempotency_key}"
        try:
            return self.client.exists(key) > 0
        except Exception as e:
            logger.warning(f"Idempotency is_in_progress error (graceful degradation): {e}", exc_info=True)
            return False

    def mark_in_progress(self, idempotency_key: str, ttl_s: int = 300, tenant: Optional[str] = None) -> None:
        """Mark request as in progress."""
        if not self.enabled or not self.client:
            return

        # Multi-tenant isolation: include tenant in in-progress key
        if tenant:
            key = f"{self.key_prefix}:tenant:{tenant}:idempotency_in_progress:{idempotency_key}"
        else:
            key = f"{self.key_prefix}:idempotency_in_progress:{idempotency_key}"
        try:
            self.client.setex(key, ttl_s, "1")
        except Exception as e:
            logger.warning(f"Idempotency mark_in_progress error (graceful degradation): {e}", exc_info=True)

    def clear_in_progress(self, idempotency_key: str, tenant: Optional[str] = None) -> None:
        """Clear in-progress marker."""
        if not self.enabled or not self.client:
            return

        # Multi-tenant isolation: include tenant in in-progress key
        if tenant:
            key = f"{self.key_prefix}:tenant:{tenant}:idempotency_in_progress:{idempotency_key}"
        else:
            key = f"{self.key_prefix}:idempotency_in_progress:{idempotency_key}"
        try:
            self.client.delete(key)
        except Exception as e:
            logger.warning(f"Idempotency clear_in_progress error (graceful degradation): {e}", exc_info=True)


