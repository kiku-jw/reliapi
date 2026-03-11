"""RapidAPI integration client for subscription management and usage tracking."""
import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import httpx
import redis

logger = logging.getLogger(__name__)


class SubscriptionTier(str, Enum):
    """RapidAPI subscription tiers."""
    FREE = "free"
    DEVELOPER = "developer"
    PRO = "pro"
    ENTERPRISE = "enterprise"


@dataclass
class SubscriptionInfo:
    """Subscription information from RapidAPI."""
    tier: SubscriptionTier
    user_id: str
    api_key_hash: str
    requests_limit: int
    requests_used: int
    valid_until: Optional[float] = None
    is_active: bool = True
    cached_at: float = field(default_factory=time.time)


class RapidAPICircuitBreaker:
    """Simple circuit breaker for RapidAPI API calls."""
    
    def __init__(self, failures_to_open: int = 3, open_ttl_s: int = 60):
        self.failures_to_open = failures_to_open
        self.open_ttl_s = open_ttl_s
        self.failure_count = 0
        self.opened_at: Optional[float] = None
        self._lock = asyncio.Lock()
    
    async def record_success(self):
        """Reset failure count on success."""
        async with self._lock:
            self.failure_count = 0
            self.opened_at = None
    
    async def record_failure(self):
        """Record a failure and check if circuit should open."""
        async with self._lock:
            self.failure_count += 1
            if self.failure_count >= self.failures_to_open:
                self.opened_at = time.time()
                logger.warning(f"RapidAPI circuit breaker opened after {self.failure_count} failures")
    
    async def is_open(self) -> bool:
        """Check if circuit is open."""
        async with self._lock:
            if self.opened_at is None:
                return False
            
            if time.time() - self.opened_at >= self.open_ttl_s:
                # Auto-close after TTL
                self.failure_count = 0
                self.opened_at = None
                logger.info("RapidAPI circuit breaker auto-closed after TTL")
                return False
            
            return True


class RapidAPIClient:
    """Client for RapidAPI Provider API integration.
    
    Handles:
    - Subscription tier detection from RapidAPI headers or API
    - API key validation
    - Usage statistics retrieval
    - Usage metrics submission (batch)
    
    Features:
    - Redis caching for tier information (TTL 10 minutes)
    - Retry logic (3 attempts, exponential backoff)
    - Circuit breaker for API calls
    - Timeout (5 seconds)
    - Fallback to 'free' tier on errors
    """
    
    # RapidAPI headers
    RAPIDAPI_PROXY_SECRET_HEADER = "X-RapidAPI-Proxy-Secret"
    RAPIDAPI_USER_HEADER = "X-RapidAPI-User"
    RAPIDAPI_SUBSCRIPTION_HEADER = "X-RapidAPI-Subscription"
    
    # Default configuration
    DEFAULT_CACHE_TTL = 600  # 10 minutes
    DEFAULT_TIMEOUT = 5.0  # 5 seconds
    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_BASE_DELAY = 1.0
    
    def __init__(
        self,
        redis_url: str,
        api_key: Optional[str] = None,
        api_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        cache_ttl: int = DEFAULT_CACHE_TTL,
        key_prefix: str = "reliapi",
    ):
        """
        Initialize RapidAPI client.
        
        Args:
            redis_url: Redis connection URL for caching
            api_key: RapidAPI Provider API key (optional, for API calls)
            api_url: RapidAPI API URL (optional)
            webhook_secret: Secret for webhook signature verification
            cache_ttl: Cache TTL in seconds (default: 600)
            key_prefix: Redis key prefix
        """
        self.api_key = api_key or os.getenv("RAPIDAPI_API_KEY")
        self.api_url = api_url or os.getenv("RAPIDAPI_API_URL", "https://rapidapi.com/api")
        self.webhook_secret = webhook_secret or os.getenv("RAPIDAPI_WEBHOOK_SECRET")
        self.cache_ttl = cache_ttl
        self.key_prefix = key_prefix
        
        # Initialize Redis client
        try:
            self.redis = redis.from_url(redis_url, decode_responses=True)
            self.redis.ping()
            self.redis_enabled = True
            logger.info(f"RapidAPIClient connected to Redis: {redis_url}")
        except Exception as e:
            self.redis = None
            self.redis_enabled = False
            logger.warning(f"RapidAPIClient Redis connection failed (using fallback): {e}")
        
        # Initialize circuit breaker
        self.circuit_breaker = RapidAPICircuitBreaker(failures_to_open=3, open_ttl_s=60)
        
        # HTTP client for API calls
        self._http_client: Optional[httpx.AsyncClient] = None
        
        # Usage batch queue
        self._usage_queue: List[Dict[str, Any]] = []
        self._usage_queue_lock = asyncio.Lock()
        self._last_usage_flush: float = time.time()
        
        # Configuration validation
        if not self.api_key:
            logger.warning("RAPIDAPI_API_KEY not set - API calls will use header-based detection only")
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.DEFAULT_TIMEOUT),
                headers={
                    "Authorization": f"Bearer {self.api_key}" if self.api_key else "",
                    "Content-Type": "application/json",
                },
            )
        return self._http_client
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
    
    def _cache_key(self, key_type: str, identifier: str) -> str:
        """Generate Redis cache key."""
        return f"{self.key_prefix}:rapidapi:{key_type}:{identifier}"
    
    def _hash_api_key(self, api_key: str) -> str:
        """Hash API key for safe storage and caching."""
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]
    
    def _validate_response(self, data: Dict[str, Any], endpoint: str) -> bool:
        """Validate RapidAPI API response structure and types.
        
        Args:
            data: Response data dictionary
            endpoint: API endpoint (for context)
            
        Returns:
            True if valid, False otherwise
        """
        if not isinstance(data, dict):
            logger.warning(f"RapidAPI response is not a dictionary: {type(data)}")
            return False
        
        # Validate based on endpoint
        if "/subscriptions/user/" in endpoint:
            # Expected: {"tier": str, "user_id": str, ...}
            if "tier" not in data:
                logger.warning(f"RapidAPI subscription response missing 'tier' field")
                return False
            tier_value = data.get("tier")
            if not isinstance(tier_value, str):
                logger.warning(f"RapidAPI subscription 'tier' is not a string: {type(tier_value)}")
                return False
            # Sanitize tier value
            tier_value = tier_value.lower().strip()
            if tier_value not in [t.value for t in SubscriptionTier]:
                logger.warning(f"RapidAPI subscription 'tier' has invalid value: {tier_value}")
                return False
        
        elif "/keys/validate/" in endpoint:
            # Expected: {"valid": bool, ...}
            if "valid" not in data:
                logger.warning(f"RapidAPI validation response missing 'valid' field")
                return False
            if not isinstance(data.get("valid"), bool):
                logger.warning(f"RapidAPI validation 'valid' is not a boolean")
                return False
        
        elif "/usage/stats" in endpoint:
            # Expected: {"requests": int, "usage_percent": float, ...}
            if "requests" in data and not isinstance(data.get("requests"), (int, float)):
                logger.warning(f"RapidAPI usage 'requests' is not a number")
                return False
            if "usage_percent" in data and not isinstance(data.get("usage_percent"), (int, float)):
                logger.warning(f"RapidAPI usage 'usage_percent' is not a number")
                return False
        
        # Sanitize string fields (prevent injection)
        for key, value in data.items():
            if isinstance(value, str):
                # Remove null bytes and control characters
                sanitized = value.replace("\x00", "").replace("\r", "").replace("\n", "")
                if sanitized != value:
                    logger.warning(f"Sanitized RapidAPI response field '{key}' (removed control characters)")
                    data[key] = sanitized
        
        return True
    
    def _sanitize_input(self, value: Any) -> Any:
        """Sanitize input data to prevent injection attacks.
        
        Args:
            value: Input value (string, dict, list, etc.)
            
        Returns:
            Sanitized value
        """
        if isinstance(value, str):
            # Remove null bytes and dangerous characters
            sanitized = value.replace("\x00", "").replace("\r", "")
            # Limit length to prevent DoS
            if len(sanitized) > 10000:
                logger.warning(f"Input string too long ({len(sanitized)}), truncating")
                sanitized = sanitized[:10000]
            return sanitized
        elif isinstance(value, dict):
            return {k: self._sanitize_input(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._sanitize_input(item) for item in value]
        else:
            return value
    
    async def _api_call_with_retry(
        self,
        method: str,
        endpoint: str,
        **kwargs,
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Make API call with retry logic and circuit breaker.
        
        Returns:
            Tuple of (success, response_data or None)
        """
        # Check circuit breaker
        if await self.circuit_breaker.is_open():
            logger.warning("RapidAPI circuit breaker is open, skipping API call")
            return False, None
        
        client = await self._get_http_client()
        last_error: Optional[Exception] = None
        
        for attempt in range(1, self.DEFAULT_MAX_RETRIES + 1):
            try:
                url = f"{self.api_url}/{endpoint.lstrip('/')}"
                response = await client.request(method, url, **kwargs)
                
                if response.status_code == 200:
                    await self.circuit_breaker.record_success()
                    response_data = response.json()
                    # Validate response structure
                    if not self._validate_response(response_data, endpoint):
                        logger.warning(f"Invalid response structure from RapidAPI for {endpoint}")
                        return False, None
                    return True, response_data
                elif response.status_code == 429:
                    # Rate limited by RapidAPI
                    retry_after = int(response.headers.get("Retry-After", 1))
                    logger.warning(f"RapidAPI rate limited, retrying after {retry_after}s")
                    await asyncio.sleep(retry_after)
                    continue
                elif response.status_code >= 500:
                    # Server error, retry
                    await self.circuit_breaker.record_failure()
                    delay = self.DEFAULT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.warning(f"RapidAPI server error {response.status_code}, retry {attempt}/{self.DEFAULT_MAX_RETRIES} in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Client error, don't retry
                    logger.error(f"RapidAPI client error {response.status_code}: {response.text}")
                    return False, None
                    
            except httpx.TimeoutException as e:
                last_error = e
                await self.circuit_breaker.record_failure()
                delay = self.DEFAULT_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"RapidAPI timeout, retry {attempt}/{self.DEFAULT_MAX_RETRIES} in {delay}s")
                await asyncio.sleep(delay)
            except Exception as e:
                last_error = e
                await self.circuit_breaker.record_failure()
                logger.error(f"RapidAPI API error: {e}")
                break
        
        if last_error:
            logger.error(f"RapidAPI API call failed after {self.DEFAULT_MAX_RETRIES} retries: {last_error}")
        return False, None
    
    def get_tier_from_headers(
        self,
        headers: Dict[str, str],
        proxy_secret: Optional[str] = None,
    ) -> Optional[Tuple[str, SubscriptionTier]]:
        """
        Extract subscription tier from RapidAPI headers.
        
        Args:
            headers: Request headers
            proxy_secret: Expected proxy secret for verification
            
        Returns:
            Tuple of (user_id, tier) or None if headers not present/invalid
        """
        # Check proxy secret if configured
        expected_secret = proxy_secret or os.getenv("RAPIDAPI_PROXY_SECRET")
        if expected_secret:
            header_secret = headers.get(self.RAPIDAPI_PROXY_SECRET_HEADER)
            if header_secret != expected_secret:
                logger.warning("RapidAPI proxy secret mismatch")
                return None
        
        # Get user ID from header
        user_id = headers.get(self.RAPIDAPI_USER_HEADER)
        if not user_id:
            return None
        
        # Get subscription tier from header
        subscription = headers.get(self.RAPIDAPI_SUBSCRIPTION_HEADER, "").lower()
        
        # Map subscription to tier
        tier_map = {
            "basic": SubscriptionTier.FREE,
            "free": SubscriptionTier.FREE,
            "pro": SubscriptionTier.DEVELOPER,
            "developer": SubscriptionTier.DEVELOPER,
            "ultra": SubscriptionTier.PRO,
            "mega": SubscriptionTier.PRO,
            "enterprise": SubscriptionTier.ENTERPRISE,
        }
        
        tier = tier_map.get(subscription, SubscriptionTier.FREE)
        logger.debug(f"RapidAPI tier from headers: user={user_id}, subscription={subscription}, tier={tier}")
        
        return user_id, tier
    
    async def get_subscription_tier(
        self,
        api_key: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> SubscriptionTier:
        """
        Get subscription tier for API key.
        
        Priority:
        1. RapidAPI headers (if present)
        2. Redis cache
        3. RapidAPI API call
        4. Fallback to 'free'
        
        Args:
            api_key: API key to check
            headers: Request headers (optional, for RapidAPI header detection)
            
        Returns:
            SubscriptionTier
        """
        # 1. Check RapidAPI headers first (most authoritative)
        if headers:
            result = self.get_tier_from_headers(headers)
            if result:
                user_id, tier = result
                # Cache the result
                await self._cache_tier(api_key, tier, user_id)
                return tier
        
        # 2. Check Redis cache
        cached_tier = await self._get_cached_tier(api_key)
        if cached_tier:
            return cached_tier
        
        # 3. Call RapidAPI API (if configured)
        if self.api_key:
            tier = await self._fetch_tier_from_api(api_key)
            if tier:
                return tier
        
        # 4. Fallback: check test key prefixes (for development)
        if api_key:
            if api_key.startswith("sk-free"):
                return SubscriptionTier.FREE
            elif api_key.startswith("sk-dev"):
                return SubscriptionTier.DEVELOPER
            elif api_key.startswith("sk-pro"):
                return SubscriptionTier.PRO
        
        # 5. Default fallback
        logger.debug(f"Using fallback tier 'free' for key: {self._hash_api_key(api_key)}")
        return SubscriptionTier.FREE
    
    async def _cache_tier(
        self,
        api_key: str,
        tier: SubscriptionTier,
        user_id: Optional[str] = None,
    ):
        """Cache tier information in Redis."""
        if not self.redis_enabled or not self.redis:
            return
        
        try:
            key = self._cache_key("tier", self._hash_api_key(api_key))
            data = {
                "tier": tier.value,
                "user_id": user_id or "",
                "cached_at": str(time.time()),
            }
            self.redis.hset(key, mapping=data)
            self.redis.expire(key, self.cache_ttl)
            logger.debug(f"Cached tier for key hash {self._hash_api_key(api_key)}: {tier.value}")
        except Exception as e:
            logger.warning(f"Failed to cache tier: {e}")
    
    async def _get_cached_tier(self, api_key: str) -> Optional[SubscriptionTier]:
        """Get cached tier from Redis."""
        from reliapi.metrics.prometheus import (
            rapidapi_tier_cache_hits_total,
            rapidapi_tier_cache_misses_total,
        )
        
        if not self.redis_enabled or not self.redis:
            return None
        
        try:
            key = self._cache_key("tier", self._hash_api_key(api_key))
            data = self.redis.hgetall(key)
            
            if not data:
                rapidapi_tier_cache_misses_total.inc()
                return None
            
            tier_value = data.get("tier")
            if tier_value:
                rapidapi_tier_cache_hits_total.inc()
                logger.debug(f"Cache hit for tier: {tier_value}")
                return SubscriptionTier(tier_value)
            
            rapidapi_tier_cache_misses_total.inc()
            return None
        except Exception as e:
            logger.warning(f"Failed to get cached tier: {e}")
            return None
    
    async def _fetch_tier_from_api(self, api_key: str) -> Optional[SubscriptionTier]:
        """Fetch tier from RapidAPI API."""
        if not self.api_key:
            return None
        
        success, data = await self._api_call_with_retry(
            "GET",
            f"/subscriptions/user/{self._hash_api_key(api_key)}",
        )
        
        if success and data:
            # Data already validated in _api_call_with_retry
            tier_value = data.get("tier", "free").lower().strip()
            tier = SubscriptionTier(tier_value) if tier_value in [t.value for t in SubscriptionTier] else SubscriptionTier.FREE
            
            # Sanitize user_id before caching
            user_id = data.get("user_id")
            if user_id:
                user_id = self._sanitize_input(user_id)
            
            # Cache the result
            await self._cache_tier(api_key, tier, user_id)
            return tier
        
        return None
    
    async def validate_api_key(
        self,
        api_key: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate API key.
        
        Args:
            api_key: API key to validate
            headers: Request headers (optional)
            
        Returns:
            Tuple of (is_valid, error_message or None)
        """
        if not api_key:
            return False, "API key is required"
        
        # Check RapidAPI headers
        if headers:
            result = self.get_tier_from_headers(headers)
            if result:
                return True, None
        
        # Check test key prefixes (for development)
        if api_key.startswith("sk-free") or api_key.startswith("sk-dev") or api_key.startswith("sk-pro"):
            return True, None
        
        # If RapidAPI API is configured, validate via API
        if self.api_key:
            success, data = await self._api_call_with_retry(
                "GET",
                f"/keys/validate/{self._hash_api_key(api_key)}",
            )
            
            if success and data:
                # Data already validated in _api_call_with_retry
                is_valid = data.get("valid", False)
                if not is_valid:
                    error_msg = data.get("error", "Invalid API key")
                    # Sanitize error message
                    error_msg = self._sanitize_input(error_msg) if isinstance(error_msg, str) else "Invalid API key"
                    return False, error_msg
                return True, None
        
        # Fallback: allow all keys (validation will happen via tier)
        return True, None
    
    async def get_usage_stats(
        self,
        api_key: str,
        period: str = "month",
    ) -> Dict[str, Any]:
        """
        Get usage statistics for API key.
        
        Args:
            api_key: API key
            period: Period for stats ('day', 'week', 'month')
            
        Returns:
            Usage statistics dict
        """
        default_stats = {
            "requests_count": 0,
            "requests_limit": 1000,  # Free tier default
            "period": period,
            "usage_percent": 0.0,
        }
        
        if not self.api_key:
            # No API configured, return from Redis if available
            if self.redis_enabled and self.redis:
                try:
                    key = self._cache_key("usage", self._hash_api_key(api_key))
                    count = int(self.redis.get(key) or 0)
                    default_stats["requests_count"] = count
                    default_stats["usage_percent"] = (count / default_stats["requests_limit"]) * 100
                except Exception as e:
                    logger.warning(f"Failed to get usage from Redis: {e}")
            
            return default_stats
        
        # Fetch from RapidAPI API
        success, data = await self._api_call_with_retry(
            "GET",
            f"/usage/{self._hash_api_key(api_key)}",
            params={"period": period},
        )
        
        if success and data:
            return data
        
        return default_stats
    
    async def record_usage(
        self,
        api_key: str,
        endpoint: str,
        latency_ms: int,
        status: str = "success",
        cost_usd: float = 0.0,
    ):
        """
        Record usage for later batch submission.
        
        Args:
            api_key: API key
            endpoint: Endpoint called
            latency_ms: Request latency in milliseconds
            status: Request status
            cost_usd: Cost in USD (for LLM requests)
        """
        usage_record = {
            "api_key_hash": self._hash_api_key(api_key),
            "endpoint": endpoint,
            "latency_ms": latency_ms,
            "status": status,
            "cost_usd": cost_usd,
            "timestamp": time.time(),
        }
        
        async with self._usage_queue_lock:
            self._usage_queue.append(usage_record)
            
            # Flush if queue is large enough or timeout reached
            should_flush = (
                len(self._usage_queue) >= 100 or
                time.time() - self._last_usage_flush >= 300  # 5 minutes
            )
            
            if should_flush:
                await self._flush_usage_queue()
        
        # Also record in Redis for local stats
        if self.redis_enabled and self.redis:
            try:
                key = self._cache_key("usage", self._hash_api_key(api_key))
                self.redis.incr(key)
                self.redis.expire(key, 86400 * 30)  # 30 days
            except Exception as e:
                logger.warning(f"Failed to record usage in Redis: {e}")
    
    async def _flush_usage_queue(self):
        """Flush usage queue to RapidAPI API."""
        if not self._usage_queue:
            return
        
        if not self.api_key:
            # Clear queue if no API configured (usage recorded in Redis only)
            self._usage_queue.clear()
            self._last_usage_flush = time.time()
            return
        
        # Copy queue and clear
        queue_copy = self._usage_queue.copy()
        self._usage_queue.clear()
        self._last_usage_flush = time.time()
        
        # Submit to RapidAPI
        success, _ = await self._api_call_with_retry(
            "POST",
            "/usage/batch",
            json={"records": queue_copy},
        )
        
        if not success:
            # Store in Redis as fallback
            if self.redis_enabled and self.redis:
                try:
                    key = self._cache_key("usage_pending", str(int(time.time())))
                    self.redis.set(key, str(queue_copy), ex=86400)  # 24 hours
                    logger.warning(f"Stored {len(queue_copy)} usage records in Redis fallback")
                except Exception as e:
                    logger.error(f"Failed to store usage in Redis fallback: {e}")
    
    def verify_webhook_signature(
        self,
        payload: bytes,
        signature: str,
    ) -> bool:
        """
        Verify webhook signature from RapidAPI.
        
        Args:
            payload: Raw request body
            signature: Signature from X-RapidAPI-Signature header
            
        Returns:
            True if signature is valid
        """
        if not self.webhook_secret:
            logger.warning("Webhook secret not configured, skipping verification")
            return True
        
        try:
            expected_signature = hmac.new(
                self.webhook_secret.encode(),
                payload,
                hashlib.sha256,
            ).hexdigest()
            
            return hmac.compare_digest(signature, expected_signature)
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            return False
    
    async def invalidate_tier_cache(self, api_key: str):
        """Invalidate cached tier for API key (called on webhook events)."""
        if not self.redis_enabled or not self.redis:
            return
        
        try:
            key = self._cache_key("tier", self._hash_api_key(api_key))
            self.redis.delete(key)
            logger.info(f"Invalidated tier cache for key hash: {self._hash_api_key(api_key)}")
        except Exception as e:
            logger.warning(f"Failed to invalidate tier cache: {e}")
    
    async def warm_cache(self, api_keys: List[str]) -> Dict[str, str]:
        """
        Pre-load tier information for a list of API keys.
        
        Args:
            api_keys: List of API keys to warm cache for
            
        Returns:
            Dict mapping API key hash to tier (or 'error' if failed)
        """
        results = {}
        
        for api_key in api_keys:
            try:
                # Check if already cached
                key_hash = self._hash_api_key(api_key)
                if self.redis_enabled and self.redis:
                    cache_key = self._cache_key("tier", key_hash)
                    cached = self.redis.get(cache_key)
                    if cached:
                        results[key_hash] = "cached"
                        continue
                
                # Fetch tier from API
                tier = await self._get_tier_from_api(api_key)
                if tier:
                    results[key_hash] = tier.value
                else:
                    results[key_hash] = "free"
                    
            except Exception as e:
                logger.warning(f"Failed to warm cache for key: {e}")
                results[self._hash_api_key(api_key)] = "error"
        
        logger.info(f"Cache warming completed for {len(api_keys)} keys")
        return results
    
    async def start_background_cache_warming(
        self,
        api_keys_source: Optional[callable] = None,
        interval_seconds: int = 300,
    ):
        """
        Start background task for periodic cache warming.
        
        Args:
            api_keys_source: Async callable that returns list of API keys to warm
            interval_seconds: Interval between warming cycles (default 5 minutes)
        """
        if api_keys_source is None:
            logger.info("No API keys source provided for cache warming")
            return
        
        async def _warming_loop():
            while True:
                try:
                    await asyncio.sleep(interval_seconds)
                    
                    # Get keys to warm
                    keys = await api_keys_source()
                    if keys:
                        await self.warm_cache(keys)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in cache warming loop: {e}")
        
        self._cache_warming_task = asyncio.create_task(_warming_loop())
        logger.info(f"Started background cache warming (interval: {interval_seconds}s)")
    
    async def stop_background_cache_warming(self):
        """Stop background cache warming task."""
        if hasattr(self, '_cache_warming_task') and self._cache_warming_task:
            self._cache_warming_task.cancel()
            try:
                await self._cache_warming_task
            except asyncio.CancelledError:
                pass
            self._cache_warming_task = None
            logger.info("Stopped background cache warming")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dict with cache statistics
        """
        from reliapi.metrics.prometheus import rapidapi_tier_cache_size
        
        stats = {
            "redis_enabled": self.redis_enabled,
            "api_configured": bool(self.api_key),
            "circuit_breaker_failures": self._circuit_breaker.failure_count,
            "usage_queue_size": len(self._usage_queue),
        }
        
        if self.redis_enabled and self.redis:
            try:
                # Count tier cache entries
                tier_pattern = self._cache_key("tier", "*")
                tier_keys = self.redis.keys(tier_pattern)
                tier_cache_count = len(tier_keys)
                stats["tier_cache_size"] = tier_cache_count
                
                # Update Prometheus gauge
                rapidapi_tier_cache_size.set(tier_cache_count)
                
                # Count usage entries
                usage_pattern = self._cache_key("usage", "*")
                usage_keys = self.redis.keys(usage_pattern)
                stats["usage_cache_size"] = len(usage_keys)
            except Exception as e:
                logger.warning(f"Failed to get cache stats: {e}")
                stats["cache_error"] = str(e)
        
        return stats


