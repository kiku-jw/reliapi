"""Shared dependencies and utilities for ReliAPI FastAPI application.

This module contains:
- Global state management (config, cache, rate limiter, etc.)
- Authentication and authorization helpers
- Client profile detection
- Configuration initialization
"""
import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from reliapi.config.loader import ConfigLoader
from reliapi.core.cache import Cache
from reliapi.core.client_profile import ClientProfile, ClientProfileManager
from reliapi.core.errors import ErrorCode
from reliapi.core.idempotency import IdempotencyManager
from reliapi.core.key_pool import KeyPoolManager, ProviderKey
from reliapi.core.rate_limiter import RateLimiter
from reliapi.core.rate_scheduler import RateScheduler
from reliapi.integrations.rapidapi import RapidAPIClient
from reliapi.integrations.rapidapi_tenant import RapidAPITenantManager
from reliapi.metrics.prometheus import rapidapi_tier_cache_total

logger = logging.getLogger(__name__)


class ConfigValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


@dataclass
class AppState:
    """Application state container for all shared components.

    This class encapsulates all global state for the ReliAPI application,
    making it easier to manage, test, and inject dependencies.
    """

    config_loader: Optional[ConfigLoader] = None
    targets: Dict[str, Dict] = field(default_factory=dict)
    cache: Optional[Cache] = None
    idempotency: Optional[IdempotencyManager] = None
    rate_limiter: Optional[RateLimiter] = None
    key_pool_manager: Optional[KeyPoolManager] = None
    rate_scheduler: Optional[RateScheduler] = None
    client_profile_manager: Optional[ClientProfileManager] = None
    rapidapi_client: Optional[RapidAPIClient] = None
    rapidapi_tenant_manager: Optional[RapidAPITenantManager] = None


# Global application state instance
app_state = AppState()


def get_app_state() -> AppState:
    """Get the global application state.

    Returns:
        AppState: The global application state instance.
    """
    return app_state


def verify_api_key(request: Request) -> Tuple[Optional[str], Optional[str], str]:
    """Verify API key from header and resolve tenant and tier.

    Priority for tier detection:
    1. RapidAPI headers (X-RapidAPI-User, X-RapidAPI-Subscription)
    2. Redis cache (from previous RapidAPI detection)
    3. Config-based tenants
    4. Test key prefixes (sk-free, sk-dev, sk-pro)
    5. Default: 'free'

    Args:
        request: FastAPI request object

    Returns:
        Tuple of (api_key, tenant_name, tier).
        tenant_name is None if multi-tenant not enabled or tenant not found.
        tier is 'free', 'developer', 'pro', or 'enterprise'.

    Raises:
        HTTPException: If API key is missing or invalid.
    """
    state = get_app_state()
    api_key = request.headers.get("X-API-Key")
    headers_dict = dict(request.headers)

    def get_tier(api_key: str, headers: Dict[str, str]) -> str:
        """Determine tier with RapidAPI priority."""
        # 1. Check RapidAPI headers first
        if state.rapidapi_client:
            result = state.rapidapi_client.get_tier_from_headers(headers)
            if result:
                user_id, tier_enum = result
                rapidapi_tier_cache_total.labels(operation="hit").inc()
                return tier_enum.value

        # 2. Use rate_limiter with RapidAPI client for cache lookup
        if state.rate_limiter and api_key:
            tier = state.rate_limiter.get_account_tier(api_key, headers, state.rapidapi_client)
            return tier

        return "free"

    # Multi-tenant mode: check tenants config
    tenants = state.config_loader.get_tenants() if state.config_loader else None
    if tenants:
        # Find tenant by API key
        for tenant_name, tenant_config in tenants.items():
            if tenant_config.api_key == api_key:
                request.state.tenant = tenant_name
                tier = get_tier(api_key, headers_dict)
                request.state.tier = tier
                return api_key, tenant_name, tier

        # Tenant not found - check global API key (backward compatibility)
        required_key = os.getenv("RELIAPI_API_KEY")
        if required_key and api_key == required_key:
            request.state.tenant = None
            tier = get_tier(api_key, headers_dict)
            request.state.tier = tier
            return api_key, None, tier

        # No matching tenant and no global key match - allow with free tier
        tier = get_tier(api_key, headers_dict)
        request.state.tier = tier
        request.state.tenant = None
        return api_key, None, tier

    # Single-tenant mode: use global API key
    if not api_key:
        # Check if RapidAPI headers are present
        if state.rapidapi_client:
            result = state.rapidapi_client.get_tier_from_headers(headers_dict)
            if result:
                user_id, tier_enum = result
                virtual_api_key = f"rapidapi:{user_id}"

                # Auto-create tenant for RapidAPI user
                if state.rapidapi_tenant_manager:
                    tenant_name = state.rapidapi_tenant_manager.ensure_tenant_exists(
                        user_id, tier_enum
                    )
                    request.state.tenant = tenant_name
                else:
                    request.state.tenant = None

                request.state.tier = tier_enum.value
                rapidapi_tier_cache_total.labels(operation="hit").inc()
                return virtual_api_key, request.state.tenant, tier_enum.value

        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "type": "client_error",
                    "code": ErrorCode.UNAUTHORIZED.value,
                    "message": "Missing X-API-Key header",
                    "retryable": False,
                    "target": None,
                    "status_code": 401,
                },
            },
        )

    tier = get_tier(api_key, headers_dict)

    # For testing: allow keys starting with sk-free/sk-dev/sk-pro
    if api_key and (
        api_key.startswith("sk-free")
        or api_key.startswith("sk-dev")
        or api_key.startswith("sk-pro")
    ):
        request.state.tenant = None
        request.state.tier = tier
        return api_key, None, tier

    # Check against required key
    required_key = os.getenv("RELIAPI_API_KEY")
    if not required_key:
        request.state.tenant = None
        request.state.tier = tier
        return api_key, None, tier

    if api_key != required_key:
        raise HTTPException(
            status_code=401,
            detail={
                "success": False,
                "error": {
                    "type": "client_error",
                    "code": ErrorCode.UNAUTHORIZED.value,
                    "message": "Invalid API key",
                    "retryable": False,
                    "target": None,
                    "status_code": 401,
                },
            },
        )

    request.state.tenant = None
    request.state.tier = tier
    return api_key, None, tier


def detect_client_profile(request: Request, tenant: Optional[str] = None) -> Optional[str]:
    """Detect client profile using priority: X-Client header > tenant.profile > default.

    Args:
        request: FastAPI request
        tenant: Tenant name (if known)

    Returns:
        Profile name or None
    """
    state = get_app_state()

    # Priority 1: X-Client header
    client_header = request.headers.get("X-Client")
    if (
        client_header
        and state.client_profile_manager
        and state.client_profile_manager.has_profile(client_header)
    ):
        return client_header

    # Priority 2: tenant.profile
    if tenant and state.config_loader:
        tenant_config = state.config_loader.get_tenant(tenant)
        profile_name = None
        if tenant_config:
            profile_name = (
                tenant_config.profile
                if hasattr(tenant_config, "profile")
                else tenant_config.get("profile")
            )
        if profile_name:
            if state.client_profile_manager and state.client_profile_manager.has_profile(
                profile_name
            ):
                return profile_name

    # Priority 3: default
    return "default"


def get_account_id(api_key: Optional[str]) -> str:
    """Generate account ID from API key hash.

    Args:
        api_key: API key string or None

    Returns:
        Hashed account ID (16 chars) or 'unknown'
    """
    if not api_key:
        return "unknown"
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def validate_startup_config(config_loader: ConfigLoader, strict: bool = True) -> List[str]:
    """Validate configuration at startup.

    Args:
        config_loader: Configuration loader
        strict: If True, fail on missing required env vars

    Returns:
        List of validation warnings (non-fatal issues)

    Raises:
        ConfigValidationError: If configuration is invalid
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. Validate required environment variables
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        warnings.append("REDIS_URL not set - Redis features will be disabled")

    rapidapi_key = os.getenv("RAPIDAPI_API_KEY")
    if not rapidapi_key:
        warnings.append("RAPIDAPI_API_KEY not set - RapidAPI tier detection may be limited")

    # 2. Validate key pool configuration
    pools_config = config_loader.get_provider_key_pools()
    if pools_config:
        seen_key_ids: Dict[str, str] = {}

        for provider, pool_config in pools_config.items():
            keys_config = pool_config.get("keys", [])

            if not keys_config:
                warnings.append(f"Key pool for provider '{provider}' has no keys configured")
                continue

            for key_config in keys_config:
                key_id = key_config.get("id")
                api_key_str = key_config.get("api_key", "")
                qps_limit = key_config.get("qps_limit")
                rate_limit = key_config.get("rate_limit", {})

                if not key_id:
                    errors.append(f"Key in provider '{provider}' is missing 'id' field")
                    continue

                full_key_id = f"{provider}:{key_id}"
                if full_key_id in seen_key_ids:
                    errors.append(f"Duplicate key ID '{key_id}' in provider '{provider}'")
                else:
                    seen_key_ids[full_key_id] = provider

                if not api_key_str:
                    errors.append(
                        f"Key '{key_id}' in provider '{provider}' is missing 'api_key' field"
                    )
                    continue

                if api_key_str.startswith("env:"):
                    env_var = api_key_str[4:]
                    if strict and not os.getenv(env_var):
                        errors.append(
                            f"Environment variable '{env_var}' not set for key "
                            f"'{key_id}' in provider '{provider}'"
                        )

                effective_qps = rate_limit.get("max_qps") or qps_limit
                if effective_qps is not None and effective_qps <= 0:
                    errors.append(
                        f"Key '{key_id}' in provider '{provider}' has invalid "
                        f"QPS limit: {effective_qps} (must be > 0)"
                    )

    # 3. Validate client profiles configuration
    profiles_config = config_loader.get_client_profiles()
    if profiles_config:
        for profile_name, profile_config in profiles_config.items():
            max_parallel = profile_config.get("max_parallel_requests")
            if max_parallel is not None and max_parallel <= 0:
                errors.append(
                    f"Client profile '{profile_name}' has invalid "
                    f"max_parallel_requests: {max_parallel} (must be > 0)"
                )

            timeout = profile_config.get("default_timeout_s")
            if timeout is not None and timeout <= 0:
                errors.append(
                    f"Client profile '{profile_name}' has invalid "
                    f"default_timeout_s: {timeout} (must be > 0)"
                )

            max_qps_tenant = profile_config.get("max_qps_per_tenant")
            if max_qps_tenant is not None and max_qps_tenant <= 0:
                errors.append(
                    f"Client profile '{profile_name}' has invalid "
                    f"max_qps_per_tenant: {max_qps_tenant} (must be > 0)"
                )

            max_qps_key = profile_config.get("max_qps_per_provider_key")
            if max_qps_key is not None and max_qps_key <= 0:
                errors.append(
                    f"Client profile '{profile_name}' has invalid "
                    f"max_qps_per_provider_key: {max_qps_key} (must be > 0)"
                )

    # Log warnings
    for warning in warnings:
        logger.warning(f"Configuration warning: {warning}")

    # Fail fast on errors
    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise ConfigValidationError(
            f"Configuration validation failed with {len(errors)} error(s): " f"{'; '.join(errors)}"
        )

    return warnings


def init_client_profile_manager(config_loader: ConfigLoader) -> ClientProfileManager:
    """Initialize ClientProfileManager from configuration.

    Args:
        config_loader: Configuration loader

    Returns:
        Initialized ClientProfileManager
    """
    profiles_config = config_loader.get_client_profiles()
    if not profiles_config:
        return ClientProfileManager()

    profiles: Dict[str, ClientProfile] = {}

    for profile_name, profile_config in profiles_config.items():
        profile = ClientProfile(
            max_parallel_requests=profile_config.get("max_parallel_requests", 10),
            max_qps_per_tenant=profile_config.get("max_qps_per_tenant"),
            max_qps_per_provider_key=profile_config.get("max_qps_per_provider_key"),
            burst_size=profile_config.get("burst_size", 5),
            default_timeout_s=profile_config.get("default_timeout_s"),
        )
        profiles[profile_name] = profile
        logger.info(f"Initialized client profile: {profile_name}")

    return ClientProfileManager(profiles)


def init_key_pool_manager(config_loader: ConfigLoader) -> Optional[KeyPoolManager]:
    """Initialize KeyPoolManager from configuration.

    Args:
        config_loader: Configuration loader

    Returns:
        Initialized KeyPoolManager or None if not configured
    """
    pools_config = config_loader.get_provider_key_pools()
    if not pools_config:
        return None

    pools: Dict[str, List[ProviderKey]] = {}

    for provider, pool_config in pools_config.items():
        keys = []
        for key_config in pool_config.get("keys", []):
            key_id = key_config.get("id")
            api_key_str = key_config.get("api_key", "")
            qps_limit = key_config.get("qps_limit")

            if not key_id:
                continue

            # Resolve API key from env if needed
            if api_key_str.startswith("env:"):
                env_var = api_key_str[4:]
                api_key = os.getenv(env_var)
                if not api_key:
                    logger.debug(f"Skipping key {key_id}: env var {env_var} not set")
                    continue
            else:
                api_key = api_key_str

            # Get rate limit config if present
            rate_limit_config = key_config.get("rate_limit", {})
            if rate_limit_config:
                qps_limit = rate_limit_config.get("max_qps") or qps_limit
                if qps_limit:
                    qps_limit = int(qps_limit)

            key = ProviderKey(
                id=key_id,
                provider=provider,
                key=api_key,
                qps_limit=qps_limit,
            )
            keys.append(key)

        if keys:
            pools[provider] = keys
            logger.info(f"Initialized key pool for {provider} with {len(keys)} keys")

    if pools:
        return KeyPoolManager(pools)
    return None
