"""Security and anti-abuse measures for ReliAPI."""
import hashlib
import logging
import re
from typing import Dict, Optional, Tuple
import redis

logger = logging.getLogger(__name__)

# Import metrics for abuse tracking
try:
    from reliapi.metrics.prometheus import abuse_patterns_total, abuse_alerts_total
except ImportError:
    # Metrics not available (e.g., during testing)
    abuse_patterns_total = None
    abuse_alerts_total = None


class SecurityManager:
    """Security manager for API key validation and masking."""

    # Valid API key patterns (OpenAI, Anthropic, Mistral)
    VALID_KEY_PATTERNS = [
        r"^sk-[a-zA-Z0-9-]{10,}$",  # OpenAI and test keys
        r"^sk-ant-[a-zA-Z0-9-]{20,}$",  # Anthropic
        r"^[a-zA-Z0-9_-]{16,}$",  # Mistral and other BYO key formats
    ]

    MAX_KEY_LENGTH = 200
    MIN_KEY_LENGTH = 10

    @staticmethod
    def validate_api_key_format(api_key: str) -> Tuple[bool, Optional[str]]:
        """
        Validate API key format.

        Args:
            api_key: API key to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not api_key:
            return False, "API key is required"

        if len(api_key) < SecurityManager.MIN_KEY_LENGTH:
            return False, f"API key too short (minimum {SecurityManager.MIN_KEY_LENGTH} characters)"

        if len(api_key) > SecurityManager.MAX_KEY_LENGTH:
            return False, f"API key too long (maximum {SecurityManager.MAX_KEY_LENGTH} characters)"

        # Check if matches any valid pattern
        for pattern in SecurityManager.VALID_KEY_PATTERNS:
            if re.match(pattern, api_key):
                return True, None

        return False, "Invalid API key format (must be OpenAI, Anthropic, or Mistral format)"

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        """
        Mask API key for logging (show only first 8 and last 4 characters).

        Args:
            api_key: API key to mask

        Returns:
            Masked API key string
        """
        if not api_key or len(api_key) < 12:
            return "***"

        return f"{api_key[:8]}...{api_key[-4:]}"

    @staticmethod
    def should_log_key(api_key: str) -> bool:
        """
        Determine if API key should be logged.

        For BYO-key: Never log full keys, only masked versions.

        Args:
            api_key: API key

        Returns:
            False (never log full keys)
        """
        return False  # Never log full keys


class FingerprintManager:
    """Fingerprint-based identity tracking for anti-abuse."""

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
            logger.info(f"FingerprintManager connected to Redis: {redis_url}")
        except Exception as e:
            self.client = None
            self.enabled = False
            logger.warning(
                f"FingerprintManager connection failed (graceful degradation): {e}", exc_info=True
            )

    def create_fingerprint(
        self,
        ip: str,
        user_agent: str,
        accept_language: str = "",
        tls_fingerprint: str = "",
    ) -> str:
        """
        Create composite fingerprint from multiple signals.

        Args:
            ip: Client IP address
            user_agent: User-Agent header
            accept_language: Accept-Language header
            tls_fingerprint: TLS fingerprint (if available)

        Returns:
            Fingerprint hash (hex string)
        """
        components = [
            hashlib.sha256(ip.encode()).hexdigest()[:16],
            hashlib.sha256(user_agent.encode()).hexdigest()[:16],
            hashlib.sha256(accept_language.encode()).hexdigest()[:16] if accept_language else "",
            hashlib.sha256(tls_fingerprint.encode()).hexdigest()[:16] if tls_fingerprint else "",
        ]
        combined = ":".join(filter(None, components))
        return hashlib.sha256(combined.encode()).hexdigest()[:32]

    def store_fingerprint(
        self,
        account_id: str,
        fingerprint: str,
        ttl_s: int = 86400,  # 24 hours
    ) -> None:
        """
        Store fingerprint for account.

        Args:
            account_id: Account identifier
            fingerprint: Fingerprint hash
            ttl_s: TTL in seconds
        """
        if not self.enabled or not self.client:
            return

        try:
            key = f"{self.key_prefix}:fingerprint:{account_id}"
            self.client.setex(key, ttl_s, fingerprint)
        except Exception as e:
            logger.warning(f"Fingerprint storage error: {e}", exc_info=True)

    def check_fingerprint_match(
        self,
        account_id: str,
        fingerprint: str,
        threshold: float = 0.5,  # 50% match required
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if fingerprint matches stored fingerprint.

        Args:
            account_id: Account identifier
            fingerprint: Current fingerprint
            threshold: Match threshold (0.0-1.0)

        Returns:
            Tuple of (matches, risk_level)
        """
        if not self.enabled or not self.client:
            return True, None  # Allow if fingerprint check unavailable

        try:
            key = f"{self.key_prefix}:fingerprint:{account_id}"
            stored = self.client.get(key)

            if not stored:
                # First time, store it
                self.store_fingerprint(account_id, fingerprint)
                return True, None

            # Simple exact match for now (can be enhanced with similarity scoring)
            if stored == fingerprint:
                return True, None
            else:
                # Fingerprint mismatch - potential risk
                return False, "HIGH_RISK"
        except Exception as e:
            logger.warning(f"Fingerprint check error: {e}", exc_info=True)
            return True, None  # Allow on error

    def record_fingerprint_mismatch(self, account_id: str) -> int:
        """
        Record fingerprint mismatch attempt.

        Args:
            account_id: Account identifier

        Returns:
            Number of mismatches in last 24 hours
        """
        if not self.enabled or not self.client:
            return 0

        try:
            key = f"{self.key_prefix}:fingerprint_mismatches:{account_id}"
            count = self.client.incr(key)
            self.client.expire(key, 86400)  # 24 hours
            return count
        except Exception as e:
            logger.warning(f"Fingerprint mismatch recording error: {e}", exc_info=True)
            return 0


class AbuseDetector:
    """Detect and prevent abuse patterns with RapidAPI tier integration."""

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
            logger.info(f"AbuseDetector connected to Redis: {redis_url}")
        except Exception as e:
            self.client = None
            self.enabled = False
            logger.warning(
                f"AbuseDetector connection failed (graceful degradation): {e}", exc_info=True
            )

        # Abuse pattern thresholds by tier
        self.abuse_thresholds = {
            "free": {
                "bypass_attempts_alert": 10,  # Alert after 10 bypass attempts
                "burst_limit_alert": 5,  # Alert after 5 burst limit violations
                "fingerprint_mismatch_alert": 3,  # Alert after 3 fingerprint mismatches
            },
            "developer": {
                "bypass_attempts_alert": 20,
                "burst_limit_alert": 10,
                "fingerprint_mismatch_alert": 5,
            },
            "pro": {
                "bypass_attempts_alert": 50,
                "burst_limit_alert": 20,
                "fingerprint_mismatch_alert": 10,
            },
            "enterprise": {
                "bypass_attempts_alert": 100,
                "burst_limit_alert": 50,
                "fingerprint_mismatch_alert": 20,
            },
        }

    def check_burst_limit(
        self,
        account_id: str,
        limit: int = 300,
        window_s: int = 600,  # 10 minutes
    ) -> Tuple[bool, Optional[str]]:
        """
        Check burst limit (requests per time window).

        Args:
            account_id: Account identifier
            limit: Maximum requests in window
            window_s: Time window in seconds

        Returns:
            Tuple of (allowed, error_message)
        """
        if not self.enabled or not self.client:
            return True, None

        try:
            key = f"{self.key_prefix}:burst:{account_id}"
            current = self.client.incr(key)
            if current == 1:
                self.client.expire(key, window_s)

            if current > limit:
                return False, "BURST_LIMIT_EXCEEDED"

            return True, None
        except Exception as e:
            logger.warning(f"Burst limit check error: {e}", exc_info=True)
            return True, None

    def check_usage_anomaly(
        self,
        account_id: str,
        multiplier: float = 3.0,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if current usage exceeds average by multiplier.

        Args:
            account_id: Account identifier
            multiplier: Multiplier for average usage (default 3x)

        Returns:
            Tuple of (allowed, error_message)
        """
        if not self.enabled or not self.client:
            return True, None

        try:
            # Get requests in last 10 minutes
            key_10min = f"{self.key_prefix}:usage:10min:{account_id}"
            requests_10min = int(self.client.get(key_10min) or 0)

            # Get average requests per 10 minutes (from 24h window)
            key_24h = f"{self.key_prefix}:usage:24h:{account_id}"
            requests_24h = int(self.client.get(key_24h) or 0)

            # Increment counters
            self.client.incr(key_10min)
            self.client.expire(key_10min, 600)  # 10 minutes

            self.client.incr(key_24h)
            self.client.expire(key_24h, 86400)  # 24 hours

            # Calculate average (24h = 144 * 10min windows)
            avg_per_10min = requests_24h / 144.0 if requests_24h > 0 else 0

            # Check if current exceeds average by multiplier
            if avg_per_10min > 0 and requests_10min > avg_per_10min * multiplier:
                return False, "USAGE_ANOMALY_DETECTED"

            return True, None
        except Exception as e:
            logger.warning(f"Usage anomaly check error: {e}", exc_info=True)
            return True, None

    def record_limit_bypass_attempt(
        self,
        account_id: str,
        ip: str,
        tier: str = "free",
    ) -> int:
        """
        Record attempt to bypass rate limits with tier-aware tracking.

        Args:
            account_id: Account identifier
            ip: Client IP address
            tier: Subscription tier (free, developer, pro, enterprise)

        Returns:
            Number of bypass attempts in last 24 hours
        """
        if not self.enabled or not self.client:
            return 0

        try:
            # Record per account
            key_account = f"{self.key_prefix}:bypass_attempts:account:{account_id}"
            count_account = self.client.incr(key_account)
            self.client.expire(key_account, 86400)  # 24 hours

            # Record per IP
            key_ip = f"{self.key_prefix}:bypass_attempts:ip:{ip}"
            count_ip = self.client.incr(key_ip)
            self.client.expire(key_ip, 86400)  # 24 hours

            # Record per tier for analytics
            key_tier = f"{self.key_prefix}:bypass_attempts:tier:{tier}"
            self.client.incr(key_tier)
            self.client.expire(key_tier, 86400)

            # Check alert threshold
            threshold = self.abuse_thresholds.get(tier, self.abuse_thresholds["free"])
            if count_account >= threshold["bypass_attempts_alert"]:
                logger.warning(
                    f"ABUSE ALERT: High bypass attempts for tier={tier}, "
                    f"account_id={account_id}, count={count_account}"
                )
                # Record alert metric
                if abuse_alerts_total:
                    abuse_alerts_total.labels(pattern_type="bypass_attempt", tier=tier).inc()

            # Record pattern metric
            if abuse_patterns_total:
                abuse_patterns_total.labels(pattern_type="bypass_attempt", tier=tier).inc()

            return max(count_account, count_ip)
        except Exception as e:
            logger.warning(f"Bypass attempt recording error: {e}", exc_info=True)
            return 0

    def record_abuse_pattern(
        self,
        pattern_type: str,
        account_id: str,
        tier: str = "free",
        metadata: Optional[Dict] = None,
    ) -> int:
        """
        Record abuse pattern with tier-aware tracking.

        Args:
            pattern_type: Type of abuse pattern (burst_limit, fingerprint_mismatch, etc.)
            account_id: Account identifier
            tier: Subscription tier
            metadata: Optional metadata

        Returns:
            Number of occurrences in last 24 hours
        """
        if not self.enabled or not self.client:
            return 0

        try:
            # Record per account
            key_account = f"{self.key_prefix}:abuse_pattern:{pattern_type}:account:{account_id}"
            count_account = self.client.incr(key_account)
            self.client.expire(key_account, 86400)  # 24 hours

            # Record per tier
            key_tier = f"{self.key_prefix}:abuse_pattern:{pattern_type}:tier:{tier}"
            self.client.incr(key_tier)
            self.client.expire(key_tier, 86400)

            # Check alert threshold
            threshold = self.abuse_thresholds.get(tier, self.abuse_thresholds["free"])
            alert_key = f"{pattern_type}_alert"
            if alert_key in threshold and count_account >= threshold[alert_key]:
                logger.warning(
                    f"ABUSE ALERT: High {pattern_type} for tier={tier}, "
                    f"account_id={account_id}, count={count_account}"
                )
                # Record alert metric
                if abuse_alerts_total:
                    abuse_alerts_total.labels(pattern_type=pattern_type, tier=tier).inc()

            # Record pattern metric
            if abuse_patterns_total:
                abuse_patterns_total.labels(pattern_type=pattern_type, tier=tier).inc()

            return count_account
        except Exception as e:
            logger.warning(f"Abuse pattern recording error: {e}", exc_info=True)
            return 0

    def get_abuse_stats(
        self,
        account_id: Optional[str] = None,
        tier: Optional[str] = None,
        window_hours: int = 24,
    ) -> Dict[str, int]:
        """
        Get abuse statistics for monitoring and alerting.

        Args:
            account_id: Optional account ID to filter
            tier: Optional tier to filter
            window_hours: Time window in hours

        Returns:
            Dictionary with abuse statistics
        """
        if not self.enabled or not self.client:
            return {}

        try:
            stats = {}

            # Get bypass attempts
            if account_id:
                key = f"{self.key_prefix}:bypass_attempts:account:{account_id}"
                stats["bypass_attempts"] = int(self.client.get(key) or 0)

            # Get abuse patterns by tier
            if tier:
                for pattern_type in ["burst_limit", "fingerprint_mismatch"]:
                    key = f"{self.key_prefix}:abuse_pattern:{pattern_type}:tier:{tier}"
                    stats[f"{pattern_type}_count"] = int(self.client.get(key) or 0)

            return stats
        except Exception as e:
            logger.warning(f"Abuse stats retrieval error: {e}", exc_info=True)
            return {}

    def should_auto_ban(
        self,
        account_id: str,
        ip: str,
        max_attempts: int = 5,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if account/IP should be auto-banned.

        Args:
            account_id: Account identifier
            ip: Client IP address
            max_attempts: Maximum bypass attempts before ban

        Returns:
            Tuple of (should_ban, reason)
        """
        if not self.enabled or not self.client:
            return False, None

        try:
            # Check account bypass attempts
            key_account = f"{self.key_prefix}:bypass_attempts:account:{account_id}"
            attempts_account = int(self.client.get(key_account) or 0)

            # Check IP bypass attempts
            key_ip = f"{self.key_prefix}:bypass_attempts:ip:{ip}"
            attempts_ip = int(self.client.get(key_ip) or 0)

            if attempts_account >= max_attempts:
                return True, f"Account banned: {attempts_account} bypass attempts"

            if attempts_ip >= max_attempts:
                return True, f"IP banned: {attempts_ip} bypass attempts"

            return False, None
        except Exception as e:
            logger.warning(f"Auto-ban check error: {e}", exc_info=True)
            return False, None
