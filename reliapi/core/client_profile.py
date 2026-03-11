"""Client profile manager for different client types (e.g., Cursor)."""
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class ClientProfile:
    """Client profile configuration."""
    
    max_parallel_requests: int = 10
    max_qps_per_tenant: Optional[float] = None
    max_qps_per_provider_key: Optional[float] = None
    burst_size: int = 5
    default_timeout_s: Optional[int] = None


class ClientProfileManager:
    """Manages client profiles and applies limits."""
    
    def __init__(self, profiles: Optional[Dict[str, ClientProfile]] = None):
        """
        Args:
            profiles: Dictionary mapping profile name to ClientProfile
        """
        self.profiles: Dict[str, ClientProfile] = profiles or {}
        # Ensure default profile exists
        if "default" not in self.profiles:
            self.profiles["default"] = ClientProfile()
    
    def get_profile(
        self,
        profile_name: Optional[str] = None,
        tenant_profile: Optional[str] = None,
    ) -> ClientProfile:
        """Get client profile by name with fallback.
        
        Args:
            profile_name: Profile name from X-Client header (highest priority)
            tenant_profile: Profile name from tenant config (fallback)
            
        Returns:
            ClientProfile instance
        """
        # Priority: profile_name (from header) > tenant_profile > default
        if profile_name and profile_name in self.profiles:
            return self.profiles[profile_name]
        
        if tenant_profile and tenant_profile in self.profiles:
            return self.profiles[tenant_profile]
        
        return self.profiles.get("default", ClientProfile())
    
    def has_profile(self, profile_name: str) -> bool:
        """Check if profile exists."""
        return profile_name in self.profiles

