"""RapidAPI tenant management for automatic tenant creation, cleanup, and isolation."""
import logging
import time
from typing import Optional, Dict, Any

import redis

from reliapi.integrations.rapidapi import SubscriptionTier

logger = logging.getLogger(__name__)


class RapidAPITenantManager:
    """Manages tenant lifecycle for RapidAPI users.
    
    Features:
    - Automatic tenant creation on first request
    - Tenant cleanup on subscription cancellation
    - Tenant isolation (separate cache/idempotency namespaces)
    - Tenant migration on tier upgrade
    """
    
    def __init__(self, redis_client: redis.Redis, key_prefix: str = "reliapi"):
        """Initialize RapidAPI tenant manager.
        
        Args:
            redis_client: Redis client instance
            key_prefix: Prefix for Redis keys
        """
        self.client = redis_client
        self.key_prefix = key_prefix
        self.tenant_prefix = f"{key_prefix}:tenant:rapidapi"
    
    def get_tenant_name(self, user_id: str) -> str:
        """Get tenant name for RapidAPI user.
        
        Args:
            user_id: RapidAPI user ID
            
        Returns:
            Tenant name (e.g., "rapidapi:user123")
        """
        return f"rapidapi:{user_id}"
    
    def create_tenant(self, user_id: str, tier: SubscriptionTier, metadata: Optional[Dict[str, Any]] = None) -> str:
        """Create or update tenant for RapidAPI user.
        
        Args:
            user_id: RapidAPI user ID
            tier: Subscription tier
            metadata: Optional metadata (subscription info, etc.)
            
        Returns:
            Tenant name
        """
        tenant_name = self.get_tenant_name(user_id)
        tenant_key = f"{self.tenant_prefix}:{user_id}"
        
        tenant_data = {
            "user_id": user_id,
            "tier": tier.value,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        
        if metadata:
            tenant_data.update(metadata)
        
        # Store tenant info in Redis
        self.client.hset(tenant_key, mapping=tenant_data)
        self.client.expire(tenant_key, 86400 * 365)  # 1 year TTL
        
        logger.info(f"Created/updated tenant {tenant_name} for RapidAPI user {user_id} (tier: {tier.value})")
        
        return tenant_name
    
    def get_tenant_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get tenant information.
        
        Args:
            user_id: RapidAPI user ID
            
        Returns:
            Tenant info dict or None if not found
        """
        tenant_key = f"{self.tenant_prefix}:{user_id}"
        data = self.client.hgetall(tenant_key)
        
        if not data:
            return None
        
        # Convert bytes to strings
        return {k.decode() if isinstance(k, bytes) else k: 
                v.decode() if isinstance(v, bytes) else v 
                for k, v in data.items()}
    
    def delete_tenant(self, user_id: str) -> bool:
        """Delete tenant and cleanup associated data.
        
        Args:
            user_id: RapidAPI user ID
            
        Returns:
            True if tenant was deleted, False if not found
        """
        tenant_name = self.get_tenant_name(user_id)
        tenant_key = f"{self.tenant_prefix}:{user_id}"
        
        # Delete tenant info
        deleted = self.client.delete(tenant_key) > 0
        
        if deleted:
            # Cleanup tenant-specific data
            self._cleanup_tenant_data(tenant_name)
            logger.info(f"Deleted tenant {tenant_name} for RapidAPI user {user_id}")
        else:
            logger.warning(f"Tenant {tenant_name} not found for deletion")
        
        return deleted
    
    def _cleanup_tenant_data(self, tenant_name: str):
        """Cleanup tenant-specific data (cache, idempotency, etc.).
        
        Args:
            tenant_name: Tenant name
        """
        # Cleanup cache keys for this tenant
        cache_pattern = f"{self.key_prefix}:cache:*:tenant:{tenant_name}:*"
        self._delete_keys_by_pattern(cache_pattern)
        
        # Cleanup idempotency keys for this tenant
        idempotency_pattern = f"{self.key_prefix}:idempotency:tenant:{tenant_name}:*"
        self._delete_keys_by_pattern(idempotency_pattern)
        
        # Cleanup rate limiter keys for this tenant
        rate_limit_pattern = f"{self.key_prefix}:ratelimit:tenant:{tenant_name}:*"
        self._delete_keys_by_pattern(rate_limit_pattern)
        
        logger.debug(f"Cleaned up data for tenant {tenant_name}")
    
    def _delete_keys_by_pattern(self, pattern: str):
        """Delete keys matching pattern (using SCAN for safety).
        
        Args:
            pattern: Redis key pattern
        """
        try:
            cursor = 0
            deleted_count = 0
            
            while True:
                cursor, keys = self.client.scan(cursor, match=pattern, count=100)
                
                if keys:
                    self.client.delete(*keys)
                    deleted_count += len(keys)
                
                if cursor == 0:
                    break
            
            if deleted_count > 0:
                logger.debug(f"Deleted {deleted_count} keys matching pattern {pattern}")
        except Exception as e:
            logger.warning(f"Error cleaning up keys matching {pattern}: {e}")
    
    def update_tenant_tier(self, user_id: str, new_tier: SubscriptionTier, metadata: Optional[Dict[str, Any]] = None):
        """Update tenant tier (for subscription upgrades/downgrades).
        
        Args:
            user_id: RapidAPI user ID
            new_tier: New subscription tier
            metadata: Optional metadata
        """
        tenant_name = self.get_tenant_name(user_id)
        tenant_key = f"{self.tenant_prefix}:{user_id}"
        
        # Update tenant info
        update_data = {
            "tier": new_tier.value,
            "updated_at": time.time(),
        }
        
        if metadata:
            update_data.update(metadata)
        
        self.client.hset(tenant_key, mapping=update_data)
        
        logger.info(f"Updated tenant {tenant_name} tier to {new_tier.value}")
    
    def ensure_tenant_exists(self, user_id: str, tier: SubscriptionTier) -> str:
        """Ensure tenant exists, creating if necessary.
        
        Args:
            user_id: RapidAPI user ID
            tier: Subscription tier
            
        Returns:
            Tenant name
        """
        tenant_info = self.get_tenant_info(user_id)
        
        if tenant_info:
            # Tenant exists, update if tier changed
            current_tier = tenant_info.get("tier")
            if current_tier != tier.value:
                self.update_tenant_tier(user_id, tier)
            return self.get_tenant_name(user_id)
        else:
            # Create new tenant
            return self.create_tenant(user_id, tier)
    
    def get_tenant_isolation_prefix(self, tenant_name: str) -> str:
        """Get isolation prefix for tenant-specific operations.
        
        Args:
            tenant_name: Tenant name
            
        Returns:
            Isolation prefix for cache/idempotency keys
        """
        return f"tenant:{tenant_name}"

