"""Prometheus metrics - minimal implementation."""
from prometheus_client import Counter, Histogram, Gauge

# Unified request counter (replaces http_requests_total and llm_requests_total)
requests_total = Counter(
    "reliapi_requests_total",
    "Total requests (HTTP and LLM)",
    ["target", "kind", "stream", "outcome", "tenant"],
)

# Request latency histogram
request_latency_ms = Histogram(
    "reliapi_request_latency_ms",
    "Request latency in milliseconds",
    ["target", "kind", "stream", "tenant"],
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
)

# Errors counter with detailed labels
errors_total = Counter(
    "reliapi_errors_total",
    "Total errors",
    ["target", "kind", "error_code", "upstream_status", "tenant"],
)

# Cache metrics
cache_hits_total = Counter(
    "reliapi_cache_hits_total",
    "Total cache hits",
    ["target", "kind", "tenant"],
)

cache_misses_total = Counter(
    "reliapi_cache_misses_total",
    "Total cache misses",
    ["target", "kind", "tenant"],
)

# Idempotency metrics
idempotent_hits_total = Counter(
    "reliapi_idempotent_hits_total",
    "Total idempotent hits",
    ["target", "kind", "tenant"],
)

# Budget events
budget_events_total = Counter(
    "reliapi_budget_events_total",
    "Total budget events (soft_cap or hard_cap)",
    ["target", "event", "tenant"],
)

# LLM cost counter (cumulative)
llm_cost_usd_total = Counter(
    "reliapi_llm_cost_usd_total",
    "Total LLM cost in USD (cumulative)",
    ["target", "tenant"],
)

# Legacy metrics (kept for backward compatibility, but deprecated)
# These will be gradually replaced by the unified metrics above
http_requests_total = Counter(
    "reliapi_http_requests_total",
    "Total HTTP proxy requests (deprecated: use reliapi_requests_total)",
    ["target", "status"],
)

llm_requests_total = Counter(
    "reliapi_llm_requests_total",
    "Total LLM proxy requests (deprecated: use reliapi_requests_total)",
    ["target", "provider", "status"],
)

latency_ms = Histogram(
    "reliapi_latency_ms",
    "Request latency in milliseconds (deprecated: use reliapi_request_latency_ms)",
    ["target", "status"],
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
)

# Key pool metrics
key_pool_requests_total = Counter(
    "reliapi_key_pool_requests_total",
    "Total requests per provider key",
    ["provider_key_id", "provider", "status"],
)

key_pool_errors_total = Counter(
    "reliapi_key_pool_errors_total",
    "Total errors per provider key",
    ["provider_key_id", "error_type"],
)

key_pool_qps = Histogram(
    "reliapi_key_pool_qps",
    "Current QPS per provider key",
    ["provider_key_id"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0],
)

key_pool_status = Histogram(
    "reliapi_key_pool_status",
    "Key status (0=active, 1=degraded, 2=exhausted, 3=banned)",
    ["provider_key_id", "status"],
    buckets=[0, 1, 2, 3],
)

# Rate scheduler metrics
rate_scheduler_429_total = Counter(
    "reliapi_rate_scheduler_429_total",
    "Total 429 responses from ReliAPI rate scheduler",
    ["source"],  # "reliapi" vs "upstream"
)

# RapidAPI integration metrics
rapidapi_api_calls_total = Counter(
    "reliapi_rapidapi_api_calls_total",
    "Total RapidAPI API calls",
    ["endpoint", "status"],  # status: "success", "error", "timeout"
)

rapidapi_api_latency_ms = Histogram(
    "reliapi_rapidapi_api_latency_ms",
    "RapidAPI API call latency in milliseconds",
    ["endpoint"],
    buckets=[10, 50, 100, 200, 500, 1000, 2000, 5000],
)

rapidapi_tier_cache_total = Counter(
    "reliapi_rapidapi_tier_cache_total",
    "RapidAPI tier cache operations",
    ["operation"],  # "hit", "miss", "set", "invalidate"
)

# More granular tier cache metrics
rapidapi_tier_cache_hits_total = Counter(
    "reliapi_rapidapi_tier_cache_hits_total",
    "Total RapidAPI tier cache hits",
)

rapidapi_tier_cache_misses_total = Counter(
    "reliapi_rapidapi_tier_cache_misses_total",
    "Total RapidAPI tier cache misses",
)

rapidapi_tier_cache_size = Gauge(
    "reliapi_rapidapi_tier_cache_size",
    "Current size of RapidAPI tier cache (number of cached keys)",
)

rapidapi_webhook_events_total = Counter(
    "reliapi_rapidapi_webhook_events_total",
    "Total RapidAPI webhook events received",
    ["event_type", "status"],  # event_type: subscription.created, etc.
)

rapidapi_usage_submissions_total = Counter(
    "reliapi_rapidapi_usage_submissions_total",
    "Total RapidAPI usage submissions",
    ["status"],  # "success", "error", "fallback"
)

rapidapi_tier_distribution = Counter(
    "reliapi_rapidapi_tier_distribution_total",
    "Distribution of requests by subscription tier",
    ["tier"],  # "free", "developer", "pro", "enterprise"
)

# Key switching metrics
key_switches_total = Counter(
    "reliapi_key_switches_total",
    "Total key switches during request processing",
    ["provider", "reason"],  # reason: "429", "5xx", "network"
)

key_switches_exhausted_total = Counter(
    "reliapi_key_switches_exhausted_total",
    "Total times key switch limit was reached",
    ["provider"],
)

# Rate scheduler bucket metrics
rate_scheduler_buckets_total = Counter(
    "reliapi_rate_scheduler_buckets_created_total",
    "Total rate scheduler buckets created",
    ["type"],  # "provider_key", "tenant", "profile"
)

rate_scheduler_buckets_current = Gauge(
    "reliapi_rate_scheduler_buckets_current",
    "Current number of active rate scheduler buckets",
    ["type"],  # "provider_key", "tenant", "profile", "total"
)

rate_scheduler_evictions_total = Counter(
    "reliapi_rate_scheduler_evictions_total",
    "Total rate scheduler bucket evictions (LRU or TTL expired)",
    ["reason"],  # "lru", "ttl_expired"
)

# Key pool exhausted alert metric
key_pool_exhausted_total = Counter(
    "reliapi_key_pool_exhausted_total",
    "Total times a key pool was exhausted (no active keys)",
    ["provider"],
)

# RouteLLM integration metrics
routellm_decisions_total = Counter(
    "reliapi_routellm_decisions_total",
    "Total RouteLLM routing decisions",
    ["route_name", "provider", "model"],
)

routellm_overrides_total = Counter(
    "reliapi_routellm_overrides_total",
    "Total RouteLLM provider/model overrides applied",
    ["override_type"],  # "provider", "model", "both"
)

# Free tier abuse detection metrics
free_tier_abuse_attempts_total = Counter(
    "reliapi_free_tier_abuse_attempts_total",
    "Total free tier abuse attempts detected",
    ["abuse_type", "tier"],  # abuse_type: "rate_limit_bypass", "burst_limit", "fingerprint_mismatch", "auto_ban"
)

# Abuse pattern metrics (for all tiers)
abuse_patterns_total = Counter(
    "reliapi_abuse_patterns_total",
    "Total abuse patterns detected by type and tier",
    ["pattern_type", "tier"],  # pattern_type: "burst_limit", "fingerprint_mismatch", "bypass_attempt"
)

abuse_alerts_total = Counter(
    "reliapi_abuse_alerts_total",
    "Total abuse alerts triggered (high abuse detected)",
    ["pattern_type", "tier"],
)

    # Legacy metric removed to avoid duplication
    # Use llm_cost_usd_total instead
    # llm_cost_usd = Histogram(...)  # Removed: conflicts with llm_cost_usd_total

