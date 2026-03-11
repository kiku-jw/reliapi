"""Rate scheduler with token bucket algorithm for smoothing bursts."""
import asyncio
import time
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)

# Configuration constants
DEFAULT_BUCKET_TTL_SECONDS = 3600  # 1 hour
MAX_BUCKETS = 1000
CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    
    max_qps: float
    burst_size: int
    tokens: float
    last_refill: float
    max_concurrent: int
    last_accessed: float = field(default_factory=time.time)
    _semaphore: Optional[asyncio.Semaphore] = None
    
    def __post_init__(self):
        """Initialize semaphore after dataclass creation."""
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrent)
    
    def refill(self, now: float):
        """Refill tokens based on elapsed time."""
        elapsed = now - self.last_refill
        if elapsed > 0:
            tokens_to_add = elapsed * self.max_qps
            self.tokens = min(self.max_qps, self.tokens + tokens_to_add)
            self.last_refill = now
    
    def consume(self, tokens: float = 1.0) -> bool:
        """Try to consume tokens from bucket.
        
        Args:
            tokens: Number of tokens to consume (default 1.0)
            
        Returns:
            True if tokens were consumed, False if bucket is empty
        """
        now = time.time()
        self.refill(now)
        self.last_accessed = now
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    def get_retry_after(self) -> float:
        """Estimate retry_after in seconds based on current token state.
        
        Returns:
            Estimated seconds until next token is available
        """
        if self.tokens >= 1.0:
            return 0.0
        
        tokens_needed = 1.0 - self.tokens
        return tokens_needed / self.max_qps
    
    async def acquire(self):
        """Acquire semaphore for concurrent request limiting."""
        if self._semaphore:
            await self._semaphore.acquire()
    
    def release(self):
        """Release semaphore."""
        if self._semaphore:
            self._semaphore.release()


class RateScheduler:
    """Rate scheduler managing multiple token buckets with LRU eviction."""
    
    def __init__(
        self, 
        max_buckets: int = MAX_BUCKETS, 
        bucket_ttl_seconds: int = DEFAULT_BUCKET_TTL_SECONDS,
        cleanup_interval_seconds: int = CLEANUP_INTERVAL_SECONDS,
    ):
        """Initialize rate scheduler.
        
        Args:
            max_buckets: Maximum number of buckets to maintain (LRU eviction)
            bucket_ttl_seconds: TTL for unused buckets (cleanup after this time)
            cleanup_interval_seconds: Interval for background cleanup task
        """
        # Use OrderedDict for LRU ordering
        self.buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()
        self.max_buckets = max_buckets
        self.bucket_ttl_seconds = bucket_ttl_seconds
        self.cleanup_interval_seconds = cleanup_interval_seconds
        
        # Track bucket types for metrics
        self._bucket_counts = {"provider_key": 0, "tenant": 0, "profile": 0, "other": 0}
        
        # Start background cleanup task
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown = False
    
    async def start_cleanup_task(self):
        """Start the background cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("Rate scheduler cleanup task started")
    
    async def stop_cleanup_task(self):
        """Stop the background cleanup task."""
        self._shutdown = True
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Rate scheduler cleanup task stopped")
    
    async def _cleanup_loop(self):
        """Background loop for periodic bucket cleanup."""
        while not self._shutdown:
            try:
                await asyncio.sleep(self.cleanup_interval_seconds)
                await self._cleanup_expired_buckets()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in rate scheduler cleanup: {e}")
    
    async def _cleanup_expired_buckets(self):
        """Remove buckets that haven't been accessed within TTL."""
        now = time.time()
        expired_keys = []
        
        async with self._lock:
            for key, bucket in self.buckets.items():
                if now - bucket.last_accessed > self.bucket_ttl_seconds:
                    expired_keys.append(key)
        
        if expired_keys:
            async with self._lock:
                for key in expired_keys:
                    if key in self.buckets:
                        self._update_bucket_count(key, -1)
                        del self.buckets[key]
            logger.info(f"Cleaned up {len(expired_keys)} expired rate limit buckets")
    
    def _update_bucket_count(self, key: str, delta: int):
        """Update bucket type counts for metrics."""
        if key.startswith("provider_key:"):
            self._bucket_counts["provider_key"] += delta
        elif key.startswith("tenant:"):
            self._bucket_counts["tenant"] += delta
        elif key.startswith("profile:"):
            self._bucket_counts["profile"] += delta
        else:
            self._bucket_counts["other"] += delta
    
    def _evict_lru_bucket(self):
        """Evict the least recently used bucket (first item in OrderedDict)."""
        if self.buckets:
            oldest_key = next(iter(self.buckets))
            self._update_bucket_count(oldest_key, -1)
            del self.buckets[oldest_key]
            logger.debug(f"Evicted LRU bucket: {oldest_key}")
    
    def get_or_create_bucket(
        self,
        key: str,
        max_qps: float,
        burst_size: int,
        max_concurrent: int = 10,
    ) -> TokenBucket:
        """Get or create token bucket for key.
        
        Args:
            key: Unique key for bucket (e.g., "provider_key_id:openai-1")
            max_qps: Maximum queries per second
            burst_size: Maximum burst size (unused in current implementation, kept for future)
            max_concurrent: Maximum concurrent requests
            
        Returns:
            TokenBucket instance
        """
        if key in self.buckets:
            # Move to end for LRU ordering (most recently used)
            self.buckets.move_to_end(key)
            return self.buckets[key]
        
        # Check if we need to evict
        while len(self.buckets) >= self.max_buckets:
            self._evict_lru_bucket()
        
        # Create new bucket
        bucket = TokenBucket(
            max_qps=max_qps,
            burst_size=burst_size,
            tokens=max_qps,  # Start with full bucket
            last_refill=time.time(),
            max_concurrent=max_concurrent,
            last_accessed=time.time(),
        )
        self.buckets[key] = bucket
        self._update_bucket_count(key, 1)
        
        return bucket
    
    def get_bucket_stats(self) -> Dict[str, int]:
        """Get statistics about current buckets.
        
        Returns:
            Dictionary with bucket counts by type
        """
        return {
            "total": len(self.buckets),
            "max_buckets": self.max_buckets,
            **self._bucket_counts,
        }
    
    async def check_rate_limit(
        self,
        provider_key_id: Optional[str] = None,
        tenant: Optional[str] = None,
        client_profile: Optional[str] = None,
        provider_key_qps: Optional[float] = None,
        tenant_qps: Optional[float] = None,
        profile_qps: Optional[float] = None,
    ) -> tuple[bool, Optional[float], Optional[str]]:
        """Check if request should be rate limited.
        
        Args:
            provider_key_id: Provider key ID (for per-key limiting)
            tenant: Tenant name (for per-tenant limiting)
            client_profile: Client profile name (for per-profile limiting)
            provider_key_qps: QPS limit for provider key
            tenant_qps: QPS limit for tenant
            profile_qps: QPS limit for client profile
            
        Returns:
            Tuple of (allowed, retry_after_s, limiting_bucket)
            - allowed: True if request can proceed, False if rate limited
            - retry_after_s: Estimated seconds until retry (if rate limited)
            - limiting_bucket: Which bucket caused the limit ("provider_key", "tenant", "profile")
        """
        async with self._lock:
            # Check provider key bucket
            if provider_key_id and provider_key_qps:
                bucket_key = f"provider_key:{provider_key_id}"
                bucket = self.get_or_create_bucket(
                    bucket_key,
                    max_qps=provider_key_qps,
                    burst_size=int(provider_key_qps * 2),
                    max_concurrent=5,
                )
                if not bucket.consume():
                    retry_after = bucket.get_retry_after()
                    return False, retry_after, "provider_key"
            
            # Check tenant bucket
            if tenant and tenant_qps:
                bucket_key = f"tenant:{tenant}"
                bucket = self.get_or_create_bucket(
                    bucket_key,
                    max_qps=tenant_qps,
                    burst_size=int(tenant_qps * 2),
                    max_concurrent=10,
                )
                if not bucket.consume():
                    retry_after = bucket.get_retry_after()
                    return False, retry_after, "tenant"
            
            # Check client profile bucket
            if client_profile and profile_qps:
                bucket_key = f"profile:{client_profile}"
                bucket = self.get_or_create_bucket(
                    bucket_key,
                    max_qps=profile_qps,
                    burst_size=int(profile_qps * 2),
                    max_concurrent=10,
                )
                if not bucket.consume():
                    retry_after = bucket.get_retry_after()
                    return False, retry_after, "profile"
            
            return True, None, None
    
    async def acquire_concurrent_slot(
        self,
        provider_key_id: Optional[str] = None,
        tenant: Optional[str] = None,
        client_profile: Optional[str] = None,
    ):
        """Acquire semaphore for concurrent request limiting.
        
        This should be called before making the actual request.
        Must be paired with release_concurrent_slot() in finally block.
        """
        buckets_to_release = []
        
        try:
            if provider_key_id:
                bucket_key = f"provider_key:{provider_key_id}"
                if bucket_key in self.buckets:
                    bucket = self.buckets[bucket_key]
                    await bucket.acquire()
                    buckets_to_release.append(bucket)
            
            if tenant:
                bucket_key = f"tenant:{tenant}"
                if bucket_key in self.buckets:
                    bucket = self.buckets[bucket_key]
                    await bucket.acquire()
                    buckets_to_release.append(bucket)
            
            if client_profile:
                bucket_key = f"profile:{client_profile}"
                if bucket_key in self.buckets:
                    bucket = self.buckets[bucket_key]
                    await bucket.acquire()
                    buckets_to_release.append(bucket)
        except Exception as e:
            # Release any acquired buckets on error
            for bucket in buckets_to_release:
                bucket.release()
            raise
        
        return buckets_to_release
    
    def release_concurrent_slots(self, buckets: list[TokenBucket]):
        """Release semaphores for concurrent request limiting."""
        for bucket in buckets:
            bucket.release()

