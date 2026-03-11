"""Pydantic schemas for ReliAPI configuration validation."""
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class CircuitConfig(BaseModel):
    """Circuit breaker configuration."""

    error_threshold: int = Field(
        default=5, gt=0, description="Number of failures before opening circuit"
    )
    cooldown_s: int = Field(
        default=60, gt=0, description="Seconds before attempting to close circuit"
    )


class CacheConfig(BaseModel):
    """Cache configuration."""

    enabled: bool = Field(default=True, description="Enable caching")
    ttl_s: int = Field(default=3600, gt=0, description="Time to live in seconds")


class LLMConfig(BaseModel):
    """LLM-specific configuration."""

    provider: Optional[str] = Field(
        default=None, description="Provider name (openai, anthropic, mistral)"
    )
    default_model: Optional[str] = Field(default=None, description="Default model name")
    max_tokens: Optional[int] = Field(default=None, gt=0, description="Maximum tokens limit")
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="Temperature limit"
    )
    soft_cost_cap_usd: Optional[float] = Field(
        default=None, ge=0.0, description="Soft cost cap (throttle if exceeded)"
    )
    hard_cost_cap_usd: Optional[float] = Field(
        default=None, ge=0.0, description="Hard cost cap (reject if exceeded)"
    )

    @field_validator("hard_cost_cap_usd")
    @classmethod
    def validate_hard_cost_cap(cls, v: Optional[float], info) -> Optional[float]:
        """Ensure hard cap >= soft cap if both are set."""
        if v is not None and "soft_cost_cap_usd" in info.data:
            soft_cap = info.data.get("soft_cost_cap_usd")
            if soft_cap is not None and v < soft_cap:
                raise ValueError(
                    f"hard_cost_cap_usd ({v}) must be >= soft_cost_cap_usd ({soft_cap})"
                )
        return v


class RetryPolicyConfig(BaseModel):
    """Retry policy configuration."""

    attempts: int = Field(default=3, gt=0, description="Number of retry attempts")
    backoff: str = Field(default="exp-jitter", description="Backoff strategy")
    base_s: float = Field(default=1.0, gt=0, description="Base delay in seconds")
    max_s: Optional[float] = Field(default=60.0, gt=0, description="Maximum delay in seconds")


class AuthConfig(BaseModel):
    """Authentication configuration."""

    type: str = Field(..., description="Auth type: bearer_env or api_key")
    env_var: Optional[str] = Field(
        default=None, description="Environment variable name for API key"
    )
    header: Optional[str] = Field(default=None, description="Header name")
    prefix: Optional[str] = Field(default=None, description="Header prefix (e.g., 'Bearer ')")


class TargetConfig(BaseModel):
    """Target (upstream) configuration."""

    base_url: str = Field(..., description="Base URL for the target")
    timeout_ms: int = Field(
        default=20000, gt=0, le=300000, description="Request timeout in milliseconds"
    )
    circuit: Optional[CircuitConfig] = Field(
        default_factory=CircuitConfig, description="Circuit breaker config"
    )
    cache: Optional[CacheConfig] = Field(default_factory=CacheConfig, description="Cache config")
    llm: Optional[LLMConfig] = Field(
        default=None, description="LLM-specific config (if applicable)"
    )
    auth: Optional[AuthConfig] = Field(default=None, description="Authentication config")
    fallback_targets: Optional[List[str]] = Field(
        default=None, description="Fallback target names (planned, not implemented)"
    )
    retry_matrix: Optional[Dict[str, RetryPolicyConfig]] = Field(
        default=None, description="Retry policies by error class"
    )


class RateLimitConfig(BaseModel):
    """Rate limit configuration for provider key."""

    max_qps: float = Field(..., gt=0, description="Maximum queries per second")
    burst_size: int = Field(default=5, gt=0, description="Maximum burst size")
    max_concurrent: int = Field(default=2, gt=0, description="Maximum concurrent requests")


class ProviderKeyConfig(BaseModel):
    """Provider key configuration."""

    id: str = Field(..., description="Unique key identifier")
    api_key: str = Field(
        ..., description="API key (can be 'env:VAR_NAME' for environment variable)"
    )
    qps_limit: Optional[int] = Field(
        default=None, gt=0, description="QPS limit for this key (deprecated: use rate_limit)"
    )
    rate_limit: Optional[RateLimitConfig] = Field(
        default=None, description="Rate limit configuration"
    )


class ProviderKeyPoolConfig(BaseModel):
    """Provider key pool configuration."""

    keys: List[ProviderKeyConfig] = Field(..., description="List of keys for this provider")


class TenantConfig(BaseModel):
    """Multi-tenant configuration.

    Each tenant has its own API key, budget caps, fallback chains, and rate limits.
    Tenants are isolated: separate cache namespaces, idempotency namespaces, and metrics.
    """

    name: Optional[str] = Field(default=None, description="Tenant name")
    api_key: str = Field(..., description="API key for this tenant (used in X-API-Key header)")
    budget_caps: Optional[Dict[str, Dict[str, float]]] = Field(
        default=None,
        description="Per-target budget caps override. Format: {target_name: {soft_cost_cap_usd: 0.01, hard_cost_cap_usd: 0.05}}",
    )
    fallback_targets: Optional[Dict[str, List[str]]] = Field(
        default=None,
        description="Per-target fallback chains override. Format: {target_name: ['fallback1', 'fallback2']}",
    )
    rate_limit_rpm: Optional[int] = Field(
        default=None,
        ge=1,
        description="Rate limit in requests per minute for this tenant (minimal, in-memory counter)",
    )
    cache_ttl_override: Optional[Dict[str, int]] = Field(
        default=None,
        description="Per-target cache TTL override in seconds. Format: {target_name: 300}",
    )
    profile: Optional[str] = Field(
        default=None, description="Client profile name for this tenant (e.g., 'cursor_default')"
    )


class ClientProfileConfig(BaseModel):
    """Client profile configuration for different client types (e.g., Cursor).

    Profiles define rate limits and behavior for specific client types.
    """

    max_parallel_requests: int = Field(default=10, gt=0, description="Maximum parallel requests")
    max_qps_per_tenant: Optional[float] = Field(
        default=None, gt=0, description="Maximum QPS per tenant"
    )
    max_qps_per_provider_key: Optional[float] = Field(
        default=None, gt=0, description="Maximum QPS per provider key"
    )
    burst_size: int = Field(default=5, gt=0, description="Burst size for rate limiting")
    default_timeout_s: Optional[float] = Field(
        default=None, gt=0, description="Default timeout in seconds"
    )


class ReliAPIConfig(BaseModel):
    """Root configuration model."""

    targets: Dict[str, TargetConfig] = Field(
        default_factory=dict, description="Target configurations"
    )
    tenants: Optional[Dict[str, TenantConfig]] = Field(
        default=None,
        description="Multi-tenant configurations. Each tenant has its own API key and isolated resources.",
    )
    provider_key_pools: Optional[Dict[str, ProviderKeyPoolConfig]] = Field(
        default=None,
        description="Provider key pools for multi-key support. If present for a provider, overrides targets[provider].auth",
    )
    client_profiles: Optional[Dict[str, ClientProfileConfig]] = Field(
        default=None,
        description="Client profiles for different client types (e.g., cursor_default). Priority: X-Client header > tenant.profile > default",
    )
