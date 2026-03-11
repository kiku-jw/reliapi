"""Provider key pool manager for multi-key support and health tracking."""
import os
import time
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


# Maximum key switches per request
MAX_KEY_SWITCHES = 3


@dataclass
class ProviderKey:
    """Provider API key with health tracking."""
    
    id: str
    provider: str  # "openai", "vertex", "anthropic"
    key: str  # actual API key (RAM-only)
    status: str = "active"  # "active", "degraded", "exhausted", "banned"
    qps_limit: Optional[int] = None
    recent_error_score: float = 0.0
    health_score: float = 1.0
    last_used_at: float = field(default_factory=time.time)
    current_qps: float = 0.0
    consecutive_errors: int = 0
    
    def calculate_load_score(self) -> float:
        """Calculate load score for key selection.
        
        Lower score = better choice.
        """
        if self.status != "active":
            return float("inf")
        
        load_from_qps = 0.0
        if self.qps_limit and self.qps_limit > 0:
            load_from_qps = self.current_qps / self.qps_limit
        
        penalty = self.recent_error_score
        
        return load_from_qps + penalty
    
    def update_health(self):
        """Update health score based on error score."""
        max_error_score = 1.0
        self.health_score = max(0.0, 1.0 - (self.recent_error_score / max_error_score))


class KeyPoolManager:
    """Manages provider key pools with health tracking and selection."""
    
    def __init__(self, pools: Optional[Dict[str, List[ProviderKey]]] = None):
        """
        Args:
            pools: Dictionary mapping provider name to list of ProviderKey objects
        """
        self.pools: Dict[str, List[ProviderKey]] = pools or {}
        self._lock = threading.Lock()
        self._qps_windows: Dict[str, List[float]] = {}  # key_id -> list of timestamps
        
        # Start background task for error score decay
        self._decay_thread = threading.Thread(target=self._decay_error_scores, daemon=True)
        self._decay_thread.start()
    
    def select_key(
        self, 
        provider: str, 
        exclude_keys: Optional[Set[str]] = None,
    ) -> Optional[ProviderKey]:
        """Select best key for provider based on load and health.
        
        Args:
            provider: Provider name (e.g., "openai")
            exclude_keys: Set of key IDs to exclude from selection (recently used in this request)
            
        Returns:
            Selected ProviderKey or None if no active keys available
        """
        with self._lock:
            pool = self.pools.get(provider)
            if not pool:
                return None
            
            # Filter active keys
            active_keys = [k for k in pool if k.status == "active"]
            
            # Exclude recently used keys if specified
            if exclude_keys:
                active_keys = [k for k in active_keys if k.id not in exclude_keys]
            
            if not active_keys:
                # If no keys after exclusion, try degraded keys
                degraded_keys = [k for k in pool if k.status == "degraded"]
                if exclude_keys:
                    degraded_keys = [k for k in degraded_keys if k.id not in exclude_keys]
                if degraded_keys:
                    active_keys = degraded_keys
                    logger.warning(f"No active keys for {provider}, falling back to degraded keys")
                else:
                    logger.error(f"No available keys for {provider} (all excluded or exhausted)")
                    return None
            
            # Select key with lowest load score
            selected = min(active_keys, key=lambda k: k.calculate_load_score())
            
            # Update usage
            selected.last_used_at = time.time()
            self._update_qps(selected.id)
            
            return selected
    
    def record_success(self, key_id: str):
        """Record successful request for key.
        
        Args:
            key_id: Key identifier
        """
        with self._lock:
            key = self._find_key(key_id)
            if not key:
                return
            
            # Reset consecutive errors
            key.consecutive_errors = 0
            
            # Gradual recovery of error score
            key.recent_error_score *= 0.95
            
            # If degraded and error score low, recover to active
            if key.status == "degraded" and key.recent_error_score < 0.3:
                key.status = "active"
                logger.info(f"Key {key_id} recovered to active status")
    
    def record_error(self, key_id: str, error_type: str, status_code: Optional[int] = None):
        """Record error for key and update health.
        
        Args:
            key_id: Key identifier
            error_type: Error type ("429", "5xx", "network", etc.)
            status_code: HTTP status code if available
        """
        with self._lock:
            key = self._find_key(key_id)
            if not key:
                return
            
            key.consecutive_errors += 1
            
            # Increase error score based on error type
            if error_type == "429" or status_code == 429:
                key.recent_error_score += 0.1
            elif error_type == "5xx" or (status_code and 500 <= status_code < 600):
                key.recent_error_score += 0.05
            else:
                key.recent_error_score += 0.02
            
            # Cap error score
            key.recent_error_score = min(1.0, key.recent_error_score)
            
            # Update health score
            key.update_health()
            
            # Status transitions
            if key.consecutive_errors >= 5:
                if key.status == "active":
                    key.status = "degraded"
                    logger.warning(f"Key {key_id} degraded due to {key.consecutive_errors} consecutive errors")
                elif key.consecutive_errors >= 10:
                    key.status = "exhausted"
                    logger.error(f"Key {key_id} exhausted due to {key.consecutive_errors} consecutive errors")
    
    def _find_key(self, key_id: str) -> Optional[ProviderKey]:
        """Find key by ID across all pools."""
        for pool in self.pools.values():
            for key in pool:
                if key.id == key_id:
                    return key
        return None
    
    def _update_qps(self, key_id: str):
        """Update QPS tracking for key."""
        now = time.time()
        window_s = 10.0  # 10 second window
        
        if key_id not in self._qps_windows:
            self._qps_windows[key_id] = []
        
        timestamps = self._qps_windows[key_id]
        
        # Remove old timestamps
        timestamps[:] = [ts for ts in timestamps if now - ts < window_s]
        
        # Add current timestamp
        timestamps.append(now)
        
        # Update key's current_qps
        key = self._find_key(key_id)
        if key:
            key.current_qps = len(timestamps) / window_s
    
    def _decay_error_scores(self):
        """Background task to decay error scores periodically."""
        while True:
            time.sleep(60)  # Every minute
            with self._lock:
                for pool in self.pools.values():
                    for key in pool:
                        # Decay error score
                        key.recent_error_score *= 0.9
                        key.update_health()
    
    def get_key_status(self, key_id: str) -> Optional[str]:
        """Get status of key by ID."""
        key = self._find_key(key_id)
        return key.status if key else None
    
    def has_pool(self, provider: str) -> bool:
        """Check if provider has key pool configured."""
        return provider in self.pools and len(self.pools[provider]) > 0
    
    def check_exhausted_pools(self) -> Dict[str, bool]:
        """Check which pools have no active keys (exhausted).
        
        Returns:
            Dictionary mapping provider name to exhausted status
        """
        with self._lock:
            result = {}
            for provider, pool in self.pools.items():
                active_count = sum(1 for k in pool if k.status == "active")
                result[provider] = active_count == 0
                if result[provider]:
                    logger.warning(f"Key pool for {provider} is exhausted (no active keys)")
            return result
    
    def get_pool_health(self, provider: str) -> Dict[str, Any]:
        """Get health summary for a provider's key pool.
        
        Args:
            provider: Provider name
            
        Returns:
            Dictionary with pool health info
        """
        with self._lock:
            pool = self.pools.get(provider)
            if not pool:
                return {"exists": False}
            
            active = sum(1 for k in pool if k.status == "active")
            degraded = sum(1 for k in pool if k.status == "degraded")
            exhausted = sum(1 for k in pool if k.status == "exhausted")
            banned = sum(1 for k in pool if k.status == "banned")
            
            avg_health = sum(k.health_score for k in pool) / len(pool) if pool else 0.0
            avg_error_score = sum(k.recent_error_score for k in pool) / len(pool) if pool else 0.0
            
            return {
                "exists": True,
                "total_keys": len(pool),
                "active": active,
                "degraded": degraded,
                "exhausted": exhausted,
                "banned": banned,
                "avg_health_score": round(avg_health, 3),
                "avg_error_score": round(avg_error_score, 3),
                "is_exhausted": active == 0,
            }
    
    def get_active_key_count(self, provider: str) -> int:
        """Get count of active keys for provider."""
        with self._lock:
            pool = self.pools.get(provider, [])
            return sum(1 for k in pool if k.status == "active")

