"""Rate limiting and abuse protection for Free tier."""
import hashlib
import time
from typing import Optional, Dict, Any
import redis
import logging

from reliapi.core.security import SecurityManager, FingerprintManager, AbuseDetector

logger = logging.getLogger(__name__)


class RateLimiter:
    """Rate limiter with IP-based and account-based limits."""
    
    def __init__(self, redis_url: str, key_prefix: str = "reliapi"):
        """
        Args:
            redis_url: Redis connection URL
            key_prefix: Prefix for Redis keys
        """
        self.key_prefix = key_prefix
        try:
            self.client = redis.from_url(redis_url, decode_responses=True)
            self.client.ping()
            self.enabled = True
            logger.info(f"RateLimiter connected to Redis: {redis_url}")
        except Exception as e:
            self.client = None
            self.enabled = False
            logger.warning(f"RateLimiter connection failed (graceful degradation): {e}", exc_info=True)
        
        # Initialize security components
        self.fingerprint_manager = FingerprintManager(redis_url, key_prefix)
        self.abuse_detector = AbuseDetector(redis_url, key_prefix)
    
    def _make_fingerprint(self, ip: str, user_agent: str, api_key: str) -> str:
        """Create sticky fingerprint from IP, User-Agent, and API key."""
        combined = f"{ip}:{user_agent}:{api_key}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    def check_ip_rate_limit(
        self, 
        ip: str, 
        limit_per_minute: int = 20,
        prefix: str = "ip",
    ) -> tuple[bool, Optional[str]]:
        """
        Check IP-based rate limit (20 req/min per IP for Free tier).
        
        Args:
            ip: Client IP address
            limit_per_minute: Maximum requests per minute
            prefix: Key prefix for rate limit (e.g., "ip", "webhook")
            
        Returns:
            Tuple of (allowed, error_message)
        """
        if not self.enabled or not self.client:
            return True, None
        
        key = f"{self.key_prefix}:ratelimit:{prefix}:{ip}"
        
        try:
            current = self.client.incr(key)
            if current == 1:
                # First request, set expiration
                self.client.expire(key, 60)
            
            if current > limit_per_minute:
                # Record bypass attempt (only for non-webhook)
                if prefix != "webhook":
                    # Get tier from context if available (default to "free")
                    tier = getattr(self, "_current_tier", "free")
                    self.abuse_detector.record_limit_bypass_attempt("ip", ip, tier=tier)
                return False, "RATE_LIMIT_EXCEEDED"
            
            return True, None
        except Exception as e:
            logger.warning(f"Rate limit check error (graceful degradation): {e}", exc_info=True)
            return True, None  # Allow on error
    
    def check_account_burst_limit(
        self, 
        account_id: str, 
        limit_per_minute: int = 500
    ) -> tuple[bool, Optional[str]]:
        """
        Check per-account burst limit.
        
        Args:
            account_id: Account identifier (API key hash or user ID)
            limit_per_minute: Maximum requests per minute
            
        Returns:
            Tuple of (allowed, error_message)
        """
        if not self.enabled or not self.client:
            return True, None
        
        key = f"{self.key_prefix}:burst:account:{account_id}"
        
        try:
            current = self.client.incr(key)
            if current == 1:
                self.client.expire(key, 60)
            
            if current > limit_per_minute:
                # Record bypass attempt
                tier = getattr(self, "_current_tier", "free")
                self.abuse_detector.record_limit_bypass_attempt(account_id, "unknown", tier=tier)
                self.abuse_detector.record_abuse_pattern("burst_limit", account_id, tier=tier)
                return False, "FREE_TIER_ABUSE"
            
            return True, None
        except Exception as e:
            logger.warning(f"Burst limit check error (graceful degradation): {e}", exc_info=True)
            return True, None
    
    def check_burst_protection(
        self,
        account_id: str,
        limit_per_10min: int = 300,
    ) -> tuple[bool, Optional[str]]:
        """
        Check burst protection (≤300 req/10min for Free tier).
        
        Args:
            account_id: Account identifier
            limit_per_10min: Maximum requests per 10 minutes
            
        Returns:
            Tuple of (allowed, error_message)
        """
        return self.abuse_detector.check_burst_limit(account_id, limit=limit_per_10min, window_s=600)
    
    def check_usage_anomaly(
        self,
        account_id: str,
        multiplier: float = 3.0,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if usage exceeds average by multiplier (3x for Free tier).
        
        Args:
            account_id: Account identifier
            multiplier: Multiplier for average usage
            
        Returns:
            Tuple of (allowed, error_message)
        """
        return self.abuse_detector.check_usage_anomaly(account_id, multiplier=multiplier)
    
    def check_fingerprint(
        self,
        account_id: str,
        ip: str,
        user_agent: str,
        accept_language: str = "",
        tls_fingerprint: str = "",
    ) -> tuple[bool, Optional[str]]:
        """
        Check fingerprint-based identity.
        
        Args:
            account_id: Account identifier
            ip: Client IP
            user_agent: User-Agent header
            accept_language: Accept-Language header
            tls_fingerprint: TLS fingerprint (if available)
            
        Returns:
            Tuple of (allowed, error_message)
        """
        fingerprint = self.fingerprint_manager.create_fingerprint(
            ip, user_agent, accept_language, tls_fingerprint
        )
        matches, risk_level = self.fingerprint_manager.check_fingerprint_match(account_id, fingerprint)
        
        if not matches:
            # Record mismatch
            mismatches = self.fingerprint_manager.record_fingerprint_mismatch(account_id)
            # Record abuse pattern
            tier = getattr(self, "_current_tier", "free")
            self.abuse_detector.record_abuse_pattern("fingerprint_mismatch", account_id, tier=tier)
            if mismatches > 5:
                return False, "FINGERPRINT_MISMATCH_BANNED"
            return False, "FINGERPRINT_MISMATCH"
        
        # Store fingerprint if first time
        self.fingerprint_manager.store_fingerprint(account_id, fingerprint)
        return True, None
    
    def check_auto_ban(
        self,
        account_id: str,
        ip: str,
        max_attempts: int = 5,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if account/IP should be auto-banned (>5 bypass attempts).
        
        Args:
            account_id: Account identifier
            ip: Client IP address
            max_attempts: Maximum bypass attempts before ban
            
        Returns:
            Tuple of (should_ban, reason)
        """
        return self.abuse_detector.should_auto_ban(account_id, ip, max_attempts=max_attempts)
    
    def check_fingerprint_limit(
        self,
        ip: str,
        user_agent: str,
        api_key: str,
        limit_per_minute: int = 20
    ) -> tuple[bool, Optional[str]]:
        """
        Check rate limit based on sticky fingerprint.
        
        Args:
            ip: Client IP
            user_agent: User-Agent header
            api_key: API key (first 8 chars for fingerprint)
            limit_per_minute: Maximum requests per minute
            
        Returns:
            Tuple of (allowed, error_message)
        """
        if not self.enabled or not self.client:
            return True, None
        
        fingerprint = self._make_fingerprint(ip, user_agent, api_key[:16] if api_key else "")
        key = f"{self.key_prefix}:ratelimit:fingerprint:{fingerprint}"
        
        try:
            current = self.client.incr(key)
            if current == 1:
                self.client.expire(key, 60)
            
            if current > limit_per_minute:
                return False, "FINGERPRINT_RATE_LIMIT_EXCEEDED"
            
            return True, None
        except Exception as e:
            logger.warning(f"Fingerprint limit check error (graceful degradation): {e}", exc_info=True)
            return True, None
    
    def check_anomaly_detector(
        self,
        account_id: str,
        threshold_multiplier: float = 1.5
    ) -> tuple[bool, Optional[str]]:
        """
        Detect anomalies: if requests in last 10 minutes > requests in last 24 hours * threshold.
        
        Args:
            account_id: Account identifier
            threshold_multiplier: Multiplier for anomaly detection (default 1.5)
            
        Returns:
            Tuple of (allowed, error_message)
        """
        if not self.enabled or not self.client:
            return True, None
        
        try:
            # Count requests in last 10 minutes
            key_10min = f"{self.key_prefix}:anomaly:10min:{account_id}"
            requests_10min = int(self.client.get(key_10min) or 0)
            
            # Count requests in last 24 hours (approximate)
            key_24h = f"{self.key_prefix}:anomaly:24h:{account_id}"
            requests_24h = int(self.client.get(key_24h) or 0)
            
            # Increment 10-minute counter
            self.client.incr(key_10min)
            self.client.expire(key_10min, 600)  # 10 minutes
            
            # Increment 24-hour counter
            self.client.incr(key_24h)
            self.client.expire(key_24h, 86400)  # 24 hours
            
            # Check anomaly
            if requests_24h > 0 and requests_10min > requests_24h * threshold_multiplier:
                # Auto-throttle: reduce rate limit
                return False, "ANOMALY_DETECTED"
            
            return True, None
        except Exception as e:
            logger.warning(f"Anomaly detection error (graceful degradation): {e}", exc_info=True)
            return True, None
    
    def get_account_tier(
        self, 
        api_key: str, 
        headers: Optional[Dict[str, str]] = None,
        rapidapi_client: Optional[Any] = None,
    ) -> str:
        """
        Determine account tier from API key.
        
        Priority:
        1. RapidAPI headers (X-RapidAPI-User, X-RapidAPI-Subscription)
        2. Redis cache (from RapidAPIClient)
        3. RapidAPI API call (if RapidAPIClient configured)
        4. Fallback: test key prefixes (sk-free, sk-dev, sk-pro)
        5. Default: 'free'
        
        Args:
            api_key: API key
            headers: Request headers (optional, for RapidAPI header detection)
            rapidapi_client: RapidAPIClient instance (optional)
            
        Returns:
            Tier: 'free', 'developer', 'pro', or 'enterprise'
        """
        # 1. Check RapidAPI headers first (synchronous check)
        if headers and rapidapi_client:
            result = rapidapi_client.get_tier_from_headers(headers)
            if result:
                user_id, tier = result
                logger.debug(f"Tier from RapidAPI headers: {tier.value}")
                return tier.value
        
        # 2. Check Redis cache (synchronous)
        if self.enabled and self.client and api_key:
            try:
                import hashlib
                key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]
                cache_key = f"{self.key_prefix}:rapidapi:tier:{key_hash}"
                cached = self.client.hgetall(cache_key)
                if cached and "tier" in cached:
                    logger.debug(f"Tier from cache: {cached['tier']}")
                    return cached["tier"]
            except Exception as e:
                logger.warning(f"Failed to get tier from cache: {e}")
        
        # 3. Fallback: check test key prefixes (for development)
        if api_key:
            if api_key.startswith('sk-free'):
                return 'free'
            elif api_key.startswith('sk-dev'):
                return 'developer'
            elif api_key.startswith('sk-pro'):
                return 'pro'
        
        # 4. Default to 'free'
        return 'free'
    
    async def get_account_tier_async(
        self, 
        api_key: str, 
        headers: Optional[Dict[str, str]] = None,
        rapidapi_client: Optional[Any] = None,
    ) -> str:
        """
        Async version of get_account_tier that can call RapidAPI API.
        
        Args:
            api_key: API key
            headers: Request headers (optional)
            rapidapi_client: RapidAPIClient instance (optional)
            
        Returns:
            Tier: 'free', 'developer', 'pro', or 'enterprise'
        """
        # Try synchronous detection first
        tier = self.get_account_tier(api_key, headers, rapidapi_client)
        
        # If we have a RapidAPIClient and got fallback result, try async API call
        if rapidapi_client and tier == 'free' and api_key:
            try:
                from reliapi.integrations.rapidapi import SubscriptionTier
                tier_enum = await rapidapi_client.get_subscription_tier(api_key, headers)
                if tier_enum:
                    return tier_enum.value
            except Exception as e:
                logger.warning(f"Failed to get tier from RapidAPI API: {e}")
        
        return tier

