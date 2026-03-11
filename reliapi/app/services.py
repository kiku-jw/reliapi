"""Service layer for ReliAPI endpoints."""
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Union, Tuple

import httpx

from reliapi.adapters.llm.factory import detect_provider, get_adapter
from reliapi.app.schemas import ErrorDetail, ErrorResponse, MetaResponse, SuccessResponse
from reliapi.core.cache import Cache
from reliapi.core.circuit_breaker import CircuitBreaker
from reliapi.core.cost_estimator import CostEstimator
from reliapi.core.errors import ErrorCode, UpstreamStatus
from reliapi.core.http_client import UpstreamHTTPClient
from reliapi.core.idempotency import IdempotencyManager
from reliapi.core.client_profile import ClientProfileManager
from reliapi.core.key_pool import KeyPoolManager, ProviderKey, MAX_KEY_SWITCHES
from reliapi.core.logging import structured_logger
from reliapi.core.rate_scheduler import RateScheduler
from reliapi.core.retry import RetryMatrix
from reliapi.metrics.prometheus import (
    budget_events_total,
    cache_hits_total,
    cache_misses_total,
    errors_total,
    idempotent_hits_total,
    key_pool_errors_total,
    key_pool_exhausted_total,
    key_pool_qps,
    key_pool_requests_total,
    key_pool_status,
    key_switches_exhausted_total,
    key_switches_total,
    llm_cost_usd_total,
    request_latency_ms,
    requests_total,
    # Legacy metrics (kept for backward compatibility)
    http_requests_total,
    latency_ms,
    llm_requests_total,
    rate_scheduler_429_total,
)
import logging

logger = logging.getLogger(__name__)


@dataclass
class KeySwitchState:
    """Tracks key switching state for a single request.

    This state should be attached to request.state.key_switch_state
    to persist across the request lifecycle.
    """

    switches: int = 0
    used_keys: Set[str] = field(default_factory=set)
    current_key_id: Optional[str] = None
    provider: Optional[str] = None

    def can_switch(self) -> bool:
        """Check if more key switches are allowed."""
        return self.switches < MAX_KEY_SWITCHES

    def record_switch(self, from_key_id: str, to_key_id: str, reason: str):
        """Record a key switch.

        Args:
            from_key_id: Key ID being switched from
            to_key_id: Key ID being switched to
            reason: Reason for switch ("429", "5xx", "network")
        """
        self.used_keys.add(from_key_id)
        self.current_key_id = to_key_id
        self.switches += 1

        # Record metrics
        if self.provider:
            key_switches_total.labels(provider=self.provider, reason=reason).inc()

    def record_exhausted(self):
        """Record that key switch limit was reached."""
        if self.provider:
            key_switches_exhausted_total.labels(provider=self.provider).inc()

    def get_excluded_keys(self) -> Set[str]:
        """Get set of keys to exclude from selection."""
        return self.used_keys

    def cleanup(self):
        """Cleanup state (called at end of request)."""
        self.used_keys.clear()
        self.current_key_id = None


def _log_and_metric_http_request(
    request_id: str,
    target_name: str,
    path: str,
    outcome: str,
    latency_ms: int,
    cache_hit: bool,
    idempotent_hit: bool,
    error_code: Optional[str] = None,
    upstream_status: Optional[int] = None,
    tenant: Optional[str] = None,
):
    """Helper to update metrics and log HTTP request."""
    # Normalize tenant for metrics (use "default" if None)
    tenant_label = tenant or "default"

    # Update unified metrics
    requests_total.labels(
        target=target_name, kind="http", stream="false", outcome=outcome, tenant=tenant_label
    ).inc()
    request_latency_ms.labels(
        target=target_name, kind="http", stream="false", tenant=tenant_label
    ).observe(latency_ms)

    if cache_hit:
        cache_hits_total.labels(target=target_name, kind="http", tenant=tenant_label).inc()
    else:
        cache_misses_total.labels(target=target_name, kind="http", tenant=tenant_label).inc()

    if idempotent_hit:
        idempotent_hits_total.labels(target=target_name, kind="http", tenant=tenant_label).inc()

    if outcome == "error" and error_code:
        # Normalize upstream_status for metrics (reduce cardinality)
        upstream_status_norm = (
            UpstreamStatus.normalize(upstream_status)
            if upstream_status
            else UpstreamStatus.UNKNOWN.value
        )
        errors_total.labels(
            target=target_name,
            kind="http",
            error_code=error_code,
            upstream_status=upstream_status_norm,
            tenant=tenant_label,
        ).inc()

    # Log request
    structured_logger.log_request(
        request_id=request_id,
        target=target_name,
        kind="http",
        stream=False,
        path=path,
        outcome=outcome,
        error_code=error_code,
        upstream_status=upstream_status,
        latency_ms=latency_ms,
        cache_hit=cache_hit,
        idempotent_hit=idempotent_hit,
        level="ERROR" if outcome == "error" else "INFO",
        tenant=tenant,
    )


def _log_and_metric_llm_request(
    request_id: str,
    target_name: str,
    provider: str,
    model: str,
    stream: bool,
    outcome: str,
    latency_ms: int,
    cache_hit: bool,
    idempotent_hit: bool,
    cost_usd: Optional[float] = None,
    error_code: Optional[str] = None,
    upstream_status: Optional[int] = None,
    tenant: Optional[str] = None,
):
    """Helper to update metrics and log LLM request."""
    # Normalize tenant for metrics (use "default" if None)
    tenant_label = tenant or "default"

    stream_str = "true" if stream else "false"
    # Update unified metrics
    requests_total.labels(
        target=target_name, kind="llm", stream=stream_str, outcome=outcome, tenant=tenant_label
    ).inc()
    request_latency_ms.labels(
        target=target_name, kind="llm", stream=stream_str, tenant=tenant_label
    ).observe(latency_ms)

    if cache_hit:
        cache_hits_total.labels(target=target_name, kind="llm", tenant=tenant_label).inc()
    else:
        cache_misses_total.labels(target=target_name, kind="llm", tenant=tenant_label).inc()

    if idempotent_hit:
        idempotent_hits_total.labels(target=target_name, kind="llm", tenant=tenant_label).inc()

    if outcome == "error" and error_code:
        # Normalize upstream_status for metrics (reduce cardinality)
        upstream_status_norm = (
            UpstreamStatus.normalize(upstream_status)
            if upstream_status
            else UpstreamStatus.UNKNOWN.value
        )
        errors_total.labels(
            target=target_name,
            kind="llm",
            error_code=error_code,
            upstream_status=upstream_status_norm,
            tenant=tenant_label,
        ).inc()

    if cost_usd and cost_usd > 0:
        llm_cost_usd_total.labels(target=target_name, tenant=tenant_label).inc(cost_usd)

    # Log request
    structured_logger.log_request(
        request_id=request_id,
        target=target_name,
        kind="llm",
        stream=stream,
        model=model,
        outcome=outcome,
        error_code=error_code,
        upstream_status=upstream_status,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
        cache_hit=cache_hit,
        idempotent_hit=idempotent_hit,
        level="ERROR" if outcome == "error" else "INFO",
        tenant=tenant,
    )


def _get_auth_from_key_pool_or_fallback(
    provider: str,
    key_pool_manager: Optional[KeyPoolManager],
    target_config: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Optional[ProviderKey], str]:
    """Get auth from key pool or fallback to targets.auth.

    Returns:
        Tuple of (auth_dict, selected_key, auth_source)
        auth_source is "pool" or "targets.auth"
    """
    # Check if key pool exists for provider
    if key_pool_manager and key_pool_manager.has_pool(provider):
        selected_key = key_pool_manager.select_key(provider)
        if selected_key:
            auth = {
                "type": "api_key",
                "header": "Authorization",
                "prefix": "Bearer ",
                "api_key": selected_key.key,
            }
            return auth, selected_key, "pool"

    # Fallback to targets.auth
    auth_config = target_config.get("auth", {})
    auth = {}
    if auth_config.get("type") == "bearer_env":
        import os

        env_var = auth_config.get("env_var")
        if env_var:
            api_key = os.getenv(env_var)
            if api_key:
                auth = {
                    "type": "api_key",
                    "header": auth_config.get("header", "Authorization"),
                    "prefix": auth_config.get("prefix", "Bearer "),
                    "api_key": api_key,
                }
    elif auth_config.get("type") == "api_key":
        auth = auth_config.copy()
        if "env_var" in auth:
            import os

            env_var = auth.pop("env_var")
            api_key = os.getenv(env_var)
            if api_key:
                auth["api_key"] = api_key

    return auth, None, "targets.auth"


def create_http_client(
    target_config: Dict[str, Any],
    target_name: str,
    key_pool_manager: Optional[KeyPoolManager] = None,
    provider: Optional[str] = None,
) -> Tuple[UpstreamHTTPClient, Optional[ProviderKey], str]:
    """Create HTTP client for target."""
    base_url = target_config["base_url"]
    timeout_ms = target_config.get("timeout_ms", 20000)
    timeout_s = timeout_ms / 1000.0

    # Circuit breaker
    circuit_config = target_config.get("circuit", {})
    circuit_breaker = CircuitBreaker(
        failures_to_open=circuit_config.get("error_threshold", 5),
        open_ttl_s=circuit_config.get("cooldown_s", 60),
    )

    # Retry matrix
    retry_config = target_config.get("retry_matrix", {})
    retry_matrix = {}
    for error_class, policy in retry_config.items():
        retry_matrix[error_class] = RetryMatrix(
            attempts=policy.get("attempts", 3),
            backoff=policy.get("backoff", "exp-jitter"),
            base_s=policy.get("base_s", 1.0),
            max_s=policy.get("max_s", 60.0),
        )

    # Auth: use key pool if available, otherwise fallback to targets.auth
    if not provider:
        # Try to detect provider from target config
        llm_config = target_config.get("llm", {})
        provider = llm_config.get("provider")

    auth, selected_key, auth_source = _get_auth_from_key_pool_or_fallback(
        provider or target_name,
        key_pool_manager,
        target_config,
    )

    client = UpstreamHTTPClient(
        base_url=base_url,
        timeout_s=timeout_s,
        retry_matrix=retry_matrix,
        circuit_breaker=circuit_breaker,
        auth=auth,
    )

    return client, selected_key, auth_source


async def handle_http_proxy(
    target_name: str,
    method: str,
    path: str,
    headers: Optional[Dict[str, str]],
    query: Optional[Dict[str, Any]],
    body: Optional[str],
    idempotency_key: Optional[str],
    cache_ttl: Optional[int],
    targets: Dict[str, Dict],
    cache: Cache,
    idempotency: IdempotencyManager,
    request_id: str,
    tenant: Optional[str] = None,
    key_pool_manager: Optional[KeyPoolManager] = None,
    rate_scheduler: Optional[RateScheduler] = None,
    client_profile_name: Optional[str] = None,
    client_profile_manager: Optional[ClientProfileManager] = None,
) -> Union[SuccessResponse, ErrorResponse]:
    """Handle HTTP proxy request."""
    start_time = time.time()
    retries = 0
    # Use KeySwitchState for proper tracking across request lifecycle
    key_switch_state = KeySwitchState()

    # Get target config
    target_config = targets.get(target_name)
    if not target_config:
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="client_error",
                code=ErrorCode.NOT_FOUND.value,
                message=f"Target '{target_name}' not found",
                retryable=False,
                target=None,
                status_code=404,
            ),
            meta=MetaResponse(
                target=None,
                cache_hit=False,
                retries=0,
                duration_ms=0,
                request_id=request_id,
                trace_id=None,
            ),
        )

    # Build full URL
    base_url = target_config["base_url"].rstrip("/")
    full_url = f"{base_url}{path}"

    # Prepare body
    body_bytes = body.encode() if body else None

    # Check cache for GET/HEAD
    cache_hit = False
    if method.upper() in ["GET", "HEAD"]:
        cache_config = target_config.get("cache", {})
        if cache_config.get("enabled", True):
            ttl = cache_ttl or cache_config.get("ttl_s", 3600)
            cached = cache.get(method, full_url, headers, body_bytes, query, tenant=tenant)
            if cached:
                cache_hit = True
                duration_ms = int((time.time() - start_time) * 1000)
                # Update metrics and log
                _log_and_metric_http_request(
                    request_id=request_id,
                    target_name=target_name,
                    path=path,
                    outcome="success",
                    latency_ms=duration_ms,
                    cache_hit=True,
                    idempotent_hit=False,
                    tenant=tenant,
                )
                # Legacy metrics
                cache_hits_total.labels(
                    target=target_name, kind="http", tenant=tenant or "default"
                ).inc()
                http_requests_total.labels(target=target_name, status="success").inc()
                latency_ms.labels(target=target_name, status="success").observe(duration_ms)
                return SuccessResponse(
                    success=True,
                    data={
                        "status_code": cached.get("status_code", 200),
                        "headers": cached.get("headers", {}),
                        "body": cached.get("body", {}),
                    },
                    meta=MetaResponse(
                        target=target_name,
                        cache_hit=True,
                        idempotent_hit=False,
                        retries=0,
                        duration_ms=duration_ms,
                        request_id=request_id,
                        trace_id=None,
                    ),
                )

    # Handle idempotency for POST/PUT/PATCH
    if idempotency_key and method.upper() in ["POST", "PUT", "PATCH"]:
        is_new, existing_id, existing_hash = idempotency.register_request(
            idempotency_key, method, full_url, headers, body_bytes, request_id, tenant=tenant
        )

        if not is_new:
            # Check if request body differs
            current_hash = idempotency.make_request_hash(method, full_url, headers, body_bytes)
            if existing_hash != current_hash:
                return ErrorResponse(
                    success=False,
                    error=ErrorDetail(
                        type="idempotency_conflict",
                        code=ErrorCode.IDEMPOTENCY_CONFLICT.value,
                        message=f"Idempotency key '{idempotency_key}' used with different request body",
                        retryable=False,
                        target=target_name,
                        status_code=409,
                        details={"existing_request_id": existing_id},
                    ),
                    meta=MetaResponse(
                        target=target_name,
                        cache_hit=False,
                        retries=0,
                        duration_ms=int((time.time() - start_time) * 1000),
                        request_id=request_id,
                        trace_id=None,
                    ),
                )

            # Get existing result (idempotent hit)
            existing_result = idempotency.get_result(idempotency_key, tenant=tenant)
            if existing_result:
                duration_ms = int((time.time() - start_time) * 1000)
                _log_and_metric_http_request(
                    request_id=request_id,
                    target_name=target_name,
                    path=path,
                    outcome="success",
                    latency_ms=duration_ms,
                    cache_hit=False,
                    idempotent_hit=True,
                    tenant=tenant,
                )
                return SuccessResponse(
                    success=True,
                    data=existing_result,
                    meta=MetaResponse(
                        target=target_name,
                        cache_hit=False,
                        idempotent_hit=True,
                        retries=0,
                        duration_ms=duration_ms,
                        request_id=request_id,
                        trace_id=None,
                    ),
                )

            # Wait for in-progress request (coalescing with exponential backoff)
            # Note: This uses polling. For high-concurrency scenarios, consider
            # using Redis pub/sub or BLPOP for more efficient event-driven coalescing.
            import asyncio

            max_wait = 30  # seconds
            waited = 0
            poll_interval = 0.05  # Start with 50ms, increase exponentially
            while idempotency.is_in_progress(idempotency_key, tenant=tenant) and waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                # Exponential backoff: increase interval up to 0.5s
                poll_interval = min(poll_interval * 1.5, 0.5)

                existing_result = idempotency.get_result(idempotency_key, tenant=tenant)
                if existing_result:
                    duration_ms = int((time.time() - start_time) * 1000)
                    _log_and_metric_http_request(
                        request_id=request_id,
                        target_name=target_name,
                        path=path,
                        outcome="success",
                        latency_ms=duration_ms,
                        cache_hit=False,
                        idempotent_hit=True,
                        tenant=tenant,
                    )
                    return SuccessResponse(
                        success=True,
                        data=existing_result,
                        meta=MetaResponse(
                            target=target_name,
                            cache_hit=False,
                            idempotent_hit=True,
                            retries=0,
                            duration_ms=duration_ms,
                            request_id=request_id,
                            trace_id=None,
                        ),
                    )

        idempotency.mark_in_progress(idempotency_key, tenant=tenant)

    # Create HTTP client (with key pool support)
    client, selected_key, auth_source = create_http_client(
        target_config, target_name, key_pool_manager=key_pool_manager
    )

    # Check rate limits before request
    if rate_scheduler and selected_key:
        # Get rate limit config from key pool
        provider_key_qps = None
        if selected_key.qps_limit:
            provider_key_qps = float(selected_key.qps_limit)

        allowed, retry_after_s, limiting_bucket = await rate_scheduler.check_rate_limit(
            provider_key_id=selected_key.id,
            tenant=tenant,
            provider_key_qps=provider_key_qps,
        )

        if not allowed:
            rate_scheduler_429_total.labels(source="reliapi").inc()
            duration_ms = int((time.time() - start_time) * 1000)
            provider_key_status = selected_key.status if selected_key else None
            return ErrorResponse(
                success=False,
                error=ErrorDetail(
                    type="rate_limit",
                    code=ErrorCode.RATE_LIMIT_RELIAPI.value,
                    message=f"Rate limit exceeded ({limiting_bucket})",
                    retryable=True,
                    source="reliapi",
                    retry_after_s=retry_after_s,
                    target=target_name,
                    status_code=429,
                    provider_key_status=provider_key_status,
                    hint="Upstream provider is being protected",
                ),
                meta=MetaResponse(
                    target=target_name,
                    cache_hit=False,
                    idempotent_hit=False,
                    retries=0,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    trace_id=None,
                ),
            )

    try:
        # Make request
        response = await client.request(
            method=method,
            path=path,
            headers=headers,
            body=body_bytes,
            params=query,
        )

        # Read response
        response_body = await response.aread()
        response_status = response.status_code
        response_headers = dict(response.headers)

        # Parse body
        try:
            body_json = json.loads(response_body.decode()) if response_body else {}
        except:
            body_json = {"raw": response_body.decode() if response_body else ""}

        result_data = {
            "status_code": response_status,
            "headers": response_headers,
            "body": body_json,
        }

        # Update key pool health on success
        if selected_key and key_pool_manager:
            key_pool_manager.record_success(selected_key.id)
            key_pool_requests_total.labels(
                provider_key_id=selected_key.id,
                provider=selected_key.provider,
                status="success",
            ).inc()
            key_pool_qps.labels(provider_key_id=selected_key.id).observe(selected_key.current_qps)
            status_value = {"active": 0, "degraded": 1, "exhausted": 2, "banned": 3}.get(
                selected_key.status, 0
            )
            key_pool_status.labels(
                provider_key_id=selected_key.id, status=selected_key.status
            ).observe(status_value)

        # Store in cache
        if method.upper() in ["GET", "HEAD"] and response_status < 400:
            cache_config = target_config.get("cache", {})
            if cache_config.get("enabled", True):
                ttl = cache_ttl or cache_config.get("ttl_s", 3600)
                cache.set(
                    method,
                    full_url,
                    headers,
                    body_bytes,
                    {
                        "status_code": response_status,
                        "headers": response_headers,
                        "body": body_json,
                    },
                    ttl_s=ttl,
                    query=query,
                    tenant=tenant,
                )

        # Store idempotency result (use same TTL as cache for consistency)
        if idempotency_key:
            idempotency_ttl = (
                cache_ttl or cache_config.get("ttl_s", 3600)
                if cache_config.get("enabled", True)
                else 3600
            )
            idempotency.store_result(
                idempotency_key, result_data, ttl_s=idempotency_ttl, tenant=tenant
            )
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        duration_ms = int((time.time() - start_time) * 1000)
        _log_and_metric_http_request(
            request_id=request_id,
            target_name=target_name,
            path=path,
            outcome="success",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,  # Will be set correctly based on actual result
            tenant=tenant,
        )
        # Legacy metrics
        http_requests_total.labels(target=target_name, status="success").inc()
        latency_ms.labels(target=target_name, status="success").observe(duration_ms)
        return SuccessResponse(
            success=True,
            data=result_data,
            meta=MetaResponse(
                target=target_name,
                cache_hit=False,
                idempotent_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
            ),
        )

    except httpx.HTTPStatusError as e:
        # HTTP error
        error_type = "upstream_error"
        error_code = ErrorCode.from_http_status(e.response.status_code)
        upstream_status_norm = UpstreamStatus.normalize(e.response.status_code)
        retryable = e.response.status_code >= 500 or e.response.status_code == 429

        # Update key pool health on error
        if selected_key and key_pool_manager:
            error_type_str = (
                "429"
                if e.response.status_code == 429
                else ("5xx" if e.response.status_code >= 500 else "other")
            )
            key_pool_manager.record_error(selected_key.id, error_type_str, e.response.status_code)
            key_pool_requests_total.labels(
                provider_key_id=selected_key.id,
                provider=selected_key.provider,
                status="error",
            ).inc()
            key_pool_errors_total.labels(
                provider_key_id=selected_key.id,
                error_type=error_type_str,
            ).inc()

            # Try key pool fallback for retryable errors (429/5xx)
            # Use KeySwitchState for proper tracking
            key_switch_state.provider = selected_key.provider
            key_switch_state.used_keys.add(selected_key.id)

            if (
                retryable
                and key_pool_manager.has_pool(selected_key.provider)
                and key_switch_state.can_switch()
            ):
                # Select new key, excluding recently used keys
                new_key = key_pool_manager.select_key(
                    selected_key.provider, exclude_keys=key_switch_state.get_excluded_keys()
                )
                if new_key and new_key.id != selected_key.id:
                    # Record the switch with reason
                    switch_reason = "429" if e.response.status_code == 429 else "5xx"
                    key_switch_state.record_switch(selected_key.id, new_key.id, switch_reason)

                    # Retry with new key (update client auth)
                    new_auth = {
                        "type": "api_key",
                        "header": "Authorization",
                        "prefix": "Bearer ",
                        "api_key": new_key.key,
                    }
                    # Update client auth
                    client.auth = new_auth
                    selected_key = new_key

                    # Retry request (this is a simple retry, not full retry logic)
                    try:
                        response = await client.request(
                            method=method,
                            path=path,
                            headers=headers,
                            body=body_bytes,
                            params=query,
                        )
                        # If successful, continue with normal flow
                        response_body = await response.aread()
                        response_status = response.status_code
                        response_headers = dict(response.headers)

                        # Parse body
                        try:
                            body_json = json.loads(response_body.decode()) if response_body else {}
                        except:
                            body_json = {"raw": response_body.decode() if response_body else ""}

                        result_data = {
                            "status_code": response_status,
                            "headers": response_headers,
                            "body": body_json,
                        }

                        # Update key pool health on success
                        if selected_key and key_pool_manager:
                            key_pool_manager.record_success(selected_key.id)
                            key_pool_requests_total.labels(
                                provider_key_id=selected_key.id,
                                provider=selected_key.provider,
                                status="success",
                            ).inc()

                        # Store in cache
                        if method.upper() in ["GET", "HEAD"] and response_status < 400:
                            cache_config = target_config.get("cache", {})
                            if cache_config.get("enabled", True):
                                ttl = cache_ttl or cache_config.get("ttl_s", 3600)
                                cache.set(
                                    method,
                                    full_url,
                                    headers,
                                    body_bytes,
                                    {
                                        "status_code": response_status,
                                        "headers": response_headers,
                                        "body": body_json,
                                    },
                                    ttl_s=ttl,
                                    query=query,
                                    tenant=tenant,
                                )

                        # Store idempotency result
                        if idempotency_key:
                            idempotency_ttl = (
                                cache_ttl or cache_config.get("ttl_s", 3600)
                                if cache_config.get("enabled", True)
                                else 3600
                            )
                            idempotency.store_result(
                                idempotency_key, result_data, ttl_s=idempotency_ttl, tenant=tenant
                            )
                            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

                        duration_ms = int((time.time() - start_time) * 1000)
                        _log_and_metric_http_request(
                            request_id=request_id,
                            target_name=target_name,
                            path=path,
                            outcome="success",
                            latency_ms=duration_ms,
                            cache_hit=False,
                            idempotent_hit=False,
                            tenant=tenant,
                        )
                        http_requests_total.labels(target=target_name, status="success").inc()
                        latency_ms.labels(target=target_name, status="success").observe(duration_ms)

                        return SuccessResponse(
                            success=True,
                            data=result_data,
                            meta=MetaResponse(
                                target=target_name,
                                cache_hit=False,
                                idempotent_hit=False,
                                retries=retries,
                                duration_ms=duration_ms,
                                request_id=request_id,
                                trace_id=None,
                            ),
                        )
                    except Exception:
                        # Fall through to error handling
                        pass

        if idempotency_key:
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        duration_ms = int((time.time() - start_time) * 1000)
        _log_and_metric_http_request(
            request_id=request_id,
            target_name=target_name,
            path=path,
            outcome="error",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,
            error_code=error_code.value,
            upstream_status=e.response.status_code,  # Actual status for logs
            tenant=tenant,
        )
        # Legacy metrics
        http_requests_total.labels(target=target_name, status="error").inc()
        errors_total.labels(
            target=target_name,
            kind="http",
            error_code=error_code.value,
            upstream_status=upstream_status_norm,
        ).inc()
        latency_ms.labels(target=target_name, status="error").observe(duration_ms)
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type=error_type,
                code=error_code.value,
                message=f"Upstream returned {e.response.status_code}",
                retryable=retryable,
                target=target_name,
                status_code=e.response.status_code,
            ),
            meta=MetaResponse(
                target=target_name,
                cache_hit=False,
                idempotent_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
            ),
        )

    except httpx.RequestError as e:
        # Network/timeout error
        if idempotency_key:
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        # Update key pool health on network error
        if selected_key and key_pool_manager:
            key_pool_manager.record_error(selected_key.id, "network", None)
            key_pool_requests_total.labels(
                provider_key_id=selected_key.id,
                provider=selected_key.provider,
                status="error",
            ).inc()
            key_pool_errors_total.labels(
                provider_key_id=selected_key.id,
                error_type="network",
            ).inc()

        duration_ms = int((time.time() - start_time) * 1000)
        error_code = ErrorCode.NETWORK_ERROR
        upstream_status_norm = UpstreamStatus.BAD_GATEWAY.value
        _log_and_metric_http_request(
            request_id=request_id,
            target_name=target_name,
            path=path,
            outcome="error",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,
            error_code=error_code.value,
            upstream_status=502,  # Actual status for logs
            tenant=tenant,
        )
        # Legacy metrics
        http_requests_total.labels(target=target_name, status="error").inc()
        errors_total.labels(
            target=target_name,
            kind="http",
            error_code=error_code.value,
            upstream_status=upstream_status_norm,
        ).inc()
        latency_ms.labels(target=target_name, status="error").observe(duration_ms)
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="upstream_error",
                code=error_code.value,
                message=f"Network error: {str(e)}",
                retryable=True,
                target=target_name,
                status_code=502,
            ),
            meta=MetaResponse(
                target=target_name,
                cache_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
            ),
        )

    except Exception as e:
        # Other errors
        if idempotency_key:
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        duration_ms = int((time.time() - start_time) * 1000)
        error_code = ErrorCode.INTERNAL_ERROR
        upstream_status_norm = UpstreamStatus.INTERNAL_SERVER_ERROR.value
        _log_and_metric_http_request(
            request_id=request_id,
            target_name=target_name,
            path=path,
            outcome="error",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,
            error_code=error_code.value,
            upstream_status=500,  # Actual status for logs
            tenant=tenant,
        )
        # Legacy metrics
        http_requests_total.labels(target=target_name, status="error").inc()
        errors_total.labels(
            target=target_name,
            kind="http",
            error_code=error_code.value,
            upstream_status=upstream_status_norm,
        ).inc()
        latency_ms.labels(target=target_name, status="error").observe(duration_ms)
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="internal_error",
                code=error_code.value,
                message=f"Internal error: {str(e)}",
                retryable=True,
                target=target_name,
                status_code=500,
            ),
            meta=MetaResponse(
                target=target_name,
                cache_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
            ),
        )

    finally:
        await client.close()


async def handle_llm_proxy(
    target_name: str,
    messages: List[Dict[str, str]],
    model: Optional[str],
    max_tokens: Optional[int],
    temperature: Optional[float],
    top_p: Optional[float],
    stop: Optional[List[str]],
    stream: Optional[bool],
    idempotency_key: Optional[str],
    cache_ttl: Optional[int],
    targets: Dict[str, Dict],
    cache: Cache,
    idempotency: IdempotencyManager,
    request_id: str,
    tenant: Optional[str] = None,
    tier: str = "free",
    key_pool_manager: Optional[KeyPoolManager] = None,
    rate_scheduler: Optional[RateScheduler] = None,
    client_profile_name: Optional[str] = None,
    client_profile_manager: Optional[ClientProfileManager] = None,
) -> Union[SuccessResponse, ErrorResponse]:
    """Handle LLM proxy request."""
    start_time = time.time()
    retries = 0
    # Use KeySwitchState for proper tracking across request lifecycle
    key_switch_state = KeySwitchState()

    # Non-streaming path only (streaming handled separately in main.py)
    # If stream is True, this function should not be called
    # (main.py routes streaming to handle_llm_stream_generator)

    # Get target config
    target_config = targets.get(target_name)
    if not target_config:
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="client_error",
                code=ErrorCode.NOT_FOUND.value,
                message=f"Target '{target_name}' not found",
                retryable=False,
                target=None,
                status_code=404,
            ),
            meta=MetaResponse(
                target=None,
                provider=None,
                model=None,
                cache_hit=False,
                idempotent_hit=False,
                retries=0,
                duration_ms=0,
                request_id=request_id,
                trace_id=None,
            ),
        )

    # Check if target has LLM config
    llm_config = target_config.get("llm", {})
    if not llm_config:
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="client_error",
                code=ErrorCode.INVALID_TARGET.value,
                message=f"Target '{target_name}' is not configured for LLM",
                retryable=False,
                target=target_name,
                status_code=400,
            ),
            meta=MetaResponse(
                target=target_name,
                provider=None,
                model=None,
                cache_hit=False,
                idempotent_hit=False,
                retries=0,
                duration_ms=int((time.time() - start_time) * 1000),
                request_id=request_id,
                trace_id=None,
            ),
        )

    # Apply config limits
    final_model = model or llm_config.get("default_model", "gpt-4")
    final_max_tokens = max_tokens
    if final_max_tokens is None:
        final_max_tokens = llm_config.get("max_tokens")
    elif llm_config.get("max_tokens"):
        final_max_tokens = min(final_max_tokens, llm_config["max_tokens"])

    final_temperature = temperature
    if final_temperature is None:
        final_temperature = llm_config.get("temperature")
    elif llm_config.get("temperature") is not None:
        final_temperature = min(final_temperature, llm_config["temperature"])

    # Get provider (explicit in config or auto-detect)
    base_url = target_config["base_url"]
    provider = llm_config.get("provider") or detect_provider(base_url)

    # Budget control: estimate cost and check caps
    cost_estimate_usd = None
    cost_policy_applied = "none"
    max_tokens_reduced = False
    original_max_tokens = None

    if provider:
        # Estimate cost before making request
        cost_estimate_usd = CostEstimator.estimate_from_messages(
            provider, final_model, messages, final_max_tokens
        )

        # Check hard cost cap (reject if exceeded)
        hard_cost_cap = llm_config.get("hard_cost_cap_usd")
        if hard_cost_cap and cost_estimate_usd and cost_estimate_usd > hard_cost_cap:
            duration_ms = int((time.time() - start_time) * 1000)
            budget_events_total.labels(
                target=target_name, event="hard_cap", tenant=tenant or "default"
            ).inc()
            error_code = ErrorCode.BUDGET_EXCEEDED
            upstream_status_norm = UpstreamStatus.BAD_REQUEST.value
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=provider,
                model=final_model,
                stream=False,
                outcome="error",
                latency_ms=duration_ms,
                cache_hit=False,
                idempotent_hit=False,
                error_code=error_code.value,
                upstream_status=400,
                tenant=tenant,
            )
            return ErrorResponse(
                success=False,
                error=ErrorDetail(
                    type="budget_error",
                    code=error_code.value,
                    message=f"Estimated cost ${cost_estimate_usd:.6f} exceeds hard cap ${hard_cost_cap:.6f}",
                    retryable=False,
                    target=target_name,
                    status_code=400,
                    details={
                        "cost_estimate_usd": cost_estimate_usd,
                        "hard_cost_cap_usd": hard_cost_cap,
                        "model": final_model,
                        "max_tokens": final_max_tokens,
                    },
                ),
                meta=MetaResponse(
                    target=target_name,
                    provider=provider,
                    model=final_model,
                    cache_hit=False,
                    idempotent_hit=False,
                    retries=0,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    trace_id=None,
                    cost_estimate_usd=cost_estimate_usd,
                    cost_policy_applied="hard_cap_rejected",
                ),
            )

        # Check soft cost cap (throttle by reducing max_tokens)
        soft_cost_cap = llm_config.get("soft_cost_cap_usd")
        if soft_cost_cap and cost_estimate_usd and cost_estimate_usd > soft_cost_cap:
            # Auto-reduce max_tokens to fit soft cap
            reduction_factor = soft_cost_cap / cost_estimate_usd
            original_max_tokens = final_max_tokens
            final_max_tokens = int(
                final_max_tokens * reduction_factor * 0.9
            )  # 0.9 for safety margin
            max_tokens_reduced = True
            cost_policy_applied = "soft_cap_throttled"
            budget_events_total.labels(
                target=target_name, event="soft_cap", tenant=tenant or "default"
            ).inc()

            # Re-estimate with reduced tokens
            cost_estimate_usd = CostEstimator.estimate_from_messages(
                provider, final_model, messages, final_max_tokens
            )

    if not provider:
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="internal_error",
                code=ErrorCode.UNKNOWN_PROVIDER.value,
                message=f"Could not determine provider for target '{target_name}'. Specify 'provider' in config or use known base_url",
                retryable=False,
                target=target_name,
                status_code=500,
            ),
            meta=MetaResponse(
                target=target_name,
                provider=None,
                model=final_model,
                cache_hit=False,
                idempotent_hit=False,
                retries=0,
                duration_ms=int((time.time() - start_time) * 1000),
                request_id=request_id,
                trace_id=None,
            ),
        )

    adapter = get_adapter(provider)
    if not adapter:
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="internal_error",
                code=ErrorCode.ADAPTER_NOT_FOUND.value,
                message=f"Adapter not found for provider '{provider}'",
                retryable=False,
                target=target_name,
                status_code=500,
            ),
            meta=MetaResponse(
                target=target_name,
                provider=provider,
                model=final_model,
                cache_hit=False,
                retries=0,
                duration_ms=int((time.time() - start_time) * 1000),
                request_id=request_id,
                trace_id=None,
            ),
        )

    # Prepare request payload
    payload = adapter.prepare_request(
        messages=messages,
        model=final_model,
        max_tokens=final_max_tokens,
        temperature=final_temperature,
        top_p=top_p,
        stop=stop,
        stream=False,  # Non-streaming path
    )

    # Determine API endpoint based on provider
    if provider == "openai":
        api_path = "/chat/completions"
    elif provider == "anthropic":
        api_path = "/messages"
    elif provider == "mistral":
        api_path = "/chat/completions"
    else:
        api_path = "/chat/completions"  # Default

    # Build cache key
    cache_key_body = json.dumps(payload, sort_keys=True)
    cache_key_bytes = cache_key_body.encode()

    # Check cache
    cache_hit = False
    cache_config = target_config.get("cache", {})
    if cache_config.get("enabled", True):
        ttl = cache_ttl or cache_config.get("ttl_s", 3600)
        cached = cache.get(
            "POST", base_url + api_path, None, cache_key_bytes, None, allow_post=True, tenant=tenant
        )
        if cached:
            cache_hit = True
            duration_ms = int((time.time() - start_time) * 1000)
            cost_usd = cached.get("cost_usd")
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=provider,
                model=final_model,
                stream=False,
                outcome="success",
                latency_ms=duration_ms,
                cache_hit=True,
                idempotent_hit=False,
                cost_usd=cost_usd,
                tenant=tenant,
            )
            # Legacy metrics
            cache_hits_total.labels(
                target=target_name, kind="llm", tenant=tenant or "default"
            ).inc()
            llm_requests_total.labels(target=target_name, provider=provider, status="success").inc()
            latency_ms.labels(target=target_name, status="success").observe(duration_ms)
            # Note: llm_cost_usd legacy metric removed, using llm_cost_usd_total instead
            return SuccessResponse(
                success=True,
                data=cached.get("body", {}),
                meta=MetaResponse(
                    target=target_name,
                    provider=provider,
                    model=final_model,
                    cache_hit=True,
                    retries=0,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    trace_id=None,
                    cost_usd=cached.get("cost_usd"),
                ),
            )

    # Handle idempotency
    if idempotency_key:
        full_url = f"{base_url}{api_path}"
        is_new, existing_id, existing_hash = idempotency.register_request(
            idempotency_key, "POST", full_url, None, cache_key_bytes, request_id, tenant=tenant
        )

        if not is_new:
            current_hash = idempotency.make_request_hash("POST", full_url, None, cache_key_bytes)
            if existing_hash != current_hash:
                return ErrorResponse(
                    success=False,
                    error=ErrorDetail(
                        type="idempotency_conflict",
                        code=ErrorCode.IDEMPOTENCY_CONFLICT.value,
                        message=f"Idempotency key '{idempotency_key}' used with different request",
                        retryable=False,
                        target=target_name,
                        status_code=409,
                        details={"existing_request_id": existing_id},
                    ),
                    meta=MetaResponse(
                        target=target_name,
                        provider=provider,
                        model=final_model,
                        cache_hit=False,
                        idempotent_hit=True,
                        retries=0,
                        duration_ms=int((time.time() - start_time) * 1000),
                        request_id=request_id,
                        trace_id=None,
                    ),
                )

            existing_result = idempotency.get_result(idempotency_key, tenant=tenant)
            if existing_result:
                duration_ms = int((time.time() - start_time) * 1000)
                cost_usd = existing_result.get("cost_usd")
                _log_and_metric_llm_request(
                    request_id=request_id,
                    target_name=target_name,
                    provider=provider,
                    model=final_model,
                    stream=False,
                    outcome="success",
                    latency_ms=duration_ms,
                    cache_hit=False,
                    idempotent_hit=True,
                    cost_usd=cost_usd,
                    tenant=tenant,
                )
                return SuccessResponse(
                    success=True,
                    data=existing_result.get("data", {}),
                    meta=MetaResponse(
                        target=target_name,
                        provider=provider,
                        model=final_model,
                        cache_hit=False,
                        idempotent_hit=True,
                        retries=0,
                        duration_ms=duration_ms,
                        request_id=request_id,
                        trace_id=None,
                        cost_usd=cost_usd,
                    ),
                )

            # Wait for in-progress request (coalescing with exponential backoff)
            # Note: This uses polling. For high-concurrency scenarios, consider
            # using Redis pub/sub or BLPOP for more efficient event-driven coalescing.
            import asyncio

            max_wait = 30
            waited = 0
            poll_interval = 0.05  # Start with 50ms, increase exponentially
            while idempotency.is_in_progress(idempotency_key, tenant=tenant) and waited < max_wait:
                await asyncio.sleep(poll_interval)
                waited += poll_interval
                # Exponential backoff: increase interval up to 0.5s
                poll_interval = min(poll_interval * 1.5, 0.5)

                existing_result = idempotency.get_result(idempotency_key, tenant=tenant)
                if existing_result:
                    duration_ms = int((time.time() - start_time) * 1000)
                    cost_usd = existing_result.get("cost_usd")
                    _log_and_metric_llm_request(
                        request_id=request_id,
                        target_name=target_name,
                        provider=provider,
                        model=final_model,
                        stream=False,
                        outcome="success",
                        latency_ms=duration_ms,
                        cache_hit=False,
                        idempotent_hit=True,
                        cost_usd=cost_usd,
                        tenant=tenant,
                    )
                    return SuccessResponse(
                        success=True,
                        data=existing_result.get("data", {}),
                        meta=MetaResponse(
                            target=target_name,
                            provider=provider,
                            model=final_model,
                            cache_hit=False,
                            idempotent_hit=True,
                            retries=0,
                            duration_ms=duration_ms,
                            request_id=request_id,
                            trace_id=None,
                            cost_usd=cost_usd,
                        ),
                    )

        idempotency.mark_in_progress(idempotency_key, tenant=tenant)

    # Create HTTP client
    # Get provider for key pool selection
    provider = llm_config.get("provider") or detect_provider(target_config.get("base_url", ""))

    # Create HTTP client (with key pool support)
    client, selected_key, auth_source = create_http_client(
        target_config, target_name, key_pool_manager=key_pool_manager, provider=provider
    )

    # Get client profile and apply limits
    profile = None
    if client_profile_manager and client_profile_name:
        profile = client_profile_manager.get_profile(profile_name=client_profile_name)

    # Check rate limits before request
    if rate_scheduler and selected_key:
        # Get rate limit config from key pool
        provider_key_qps = None
        if selected_key.qps_limit:
            provider_key_qps = float(selected_key.qps_limit)

        # Apply profile limits if available
        if profile and profile.max_qps_per_provider_key:
            provider_key_qps = (
                min(provider_key_qps, profile.max_qps_per_provider_key)
                if provider_key_qps
                else profile.max_qps_per_provider_key
            )

        tenant_qps = None
        if profile and profile.max_qps_per_tenant:
            tenant_qps = profile.max_qps_per_tenant

        profile_qps = None
        if profile and profile.max_qps_per_provider_key:
            profile_qps = profile.max_qps_per_provider_key

        allowed, retry_after_s, limiting_bucket = await rate_scheduler.check_rate_limit(
            provider_key_id=selected_key.id,
            tenant=tenant,
            client_profile=client_profile_name,
            provider_key_qps=provider_key_qps,
            tenant_qps=tenant_qps,
            profile_qps=profile_qps,
        )

        if not allowed:
            rate_scheduler_429_total.labels(source="reliapi").inc()
            duration_ms = int((time.time() - start_time) * 1000)
            provider_key_status = selected_key.status if selected_key else None
            return ErrorResponse(
                success=False,
                error=ErrorDetail(
                    type="rate_limit",
                    code=ErrorCode.RATE_LIMIT_RELIAPI.value,
                    message=f"Rate limit exceeded ({limiting_bucket})",
                    retryable=True,
                    source="reliapi",
                    retry_after_s=retry_after_s,
                    target=target_name,
                    status_code=429,
                    provider_key_status=provider_key_status,
                    hint="Upstream provider is being protected",
                ),
                meta=MetaResponse(
                    target=target_name,
                    provider=provider,
                    model=final_model,
                    cache_hit=False,
                    idempotent_hit=False,
                    retries=0,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    trace_id=None,
                ),
            )

    try:
        # Make request
        response = await client.request(
            method="POST",
            path=api_path,
            headers={"Content-Type": "application/json"},
            body=cache_key_bytes,
            params=None,
        )

        # Read response
        response_body = await response.aread()
        response_status = response.status_code

        if response_status >= 400:
            # Check if we should try fallback
            fallback_targets = target_config.get("fallback_targets", [])

            # Free tier: No chaining fallbacks (>1 provider)
            if tier == "free" and len(fallback_targets) > 0:
                # Free tier cannot use fallback chains
                pass  # Skip fallback for free tier
            else:
                should_fallback = (
                    fallback_targets
                    and (response_status >= 500 or response_status == 429)
                    and response_status < 600  # Only retryable errors
                )

            if should_fallback:
                # Try fallback targets in order
                for fallback_target_name in fallback_targets:
                    fallback_config = targets.get(fallback_target_name)
                    if not fallback_config:
                        continue

                    # Check if fallback has LLM config
                    fallback_llm_config = fallback_config.get("llm", {})
                    if not fallback_llm_config:
                        continue

                    # Try fallback target
                    try:
                        fallback_result = await handle_llm_proxy(
                            target_name=fallback_target_name,
                            messages=messages,
                            model=model,
                            max_tokens=max_tokens,
                            temperature=temperature,
                            top_p=top_p,
                            stop=stop,
                            stream=False,  # No fallback for streaming
                            idempotency_key=None,  # Don't reuse idempotency key for fallback
                            cache_ttl=cache_ttl,
                            targets=targets,
                            cache=cache,
                            idempotency=idempotency,
                            request_id=request_id,
                            tenant=tenant,
                            tier=tier,  # Pass tier to fallback handler
                        )

                        if fallback_result.success:
                            # Update meta to indicate fallback was used
                            if isinstance(fallback_result, SuccessResponse):
                                fallback_result.meta.fallback_used = True
                                fallback_result.meta.fallback_target = fallback_target_name
                            return fallback_result
                    except Exception as e:
                        # Continue to next fallback
                        continue

            # Update key pool health on error
            if selected_key and key_pool_manager:
                error_type_str = (
                    "429"
                    if response_status == 429
                    else ("5xx" if response_status >= 500 else "other")
                )
                key_pool_manager.record_error(selected_key.id, error_type_str, response_status)
                key_pool_requests_total.labels(
                    provider_key_id=selected_key.id,
                    provider=selected_key.provider,
                    status="error",
                ).inc()
                key_pool_errors_total.labels(
                    provider_key_id=selected_key.id,
                    error_type=error_type_str,
                ).inc()

                # Try key pool fallback for retryable errors (429/5xx)
                # Use KeySwitchState for proper tracking
                key_switch_state.provider = selected_key.provider
                key_switch_state.used_keys.add(selected_key.id)

                retryable_error = response_status >= 500 or response_status == 429
                if (
                    retryable_error
                    and key_pool_manager.has_pool(selected_key.provider)
                    and key_switch_state.can_switch()
                ):
                    # Select new key, excluding recently used keys
                    new_key = key_pool_manager.select_key(
                        selected_key.provider, exclude_keys=key_switch_state.get_excluded_keys()
                    )
                    if new_key and new_key.id != selected_key.id:
                        # Record the switch with reason
                        switch_reason = "429" if response_status == 429 else "5xx"
                        key_switch_state.record_switch(selected_key.id, new_key.id, switch_reason)

                        # Retry with new key (update client auth)
                        new_auth = {
                            "type": "api_key",
                            "header": "Authorization",
                            "prefix": "Bearer ",
                            "api_key": new_key.key,
                        }
                        client.auth = new_auth
                        selected_key = new_key

                        # Retry request
                        try:
                            response = await client.request(
                                method="POST",
                                path=api_path,
                                headers={"Content-Type": "application/json"},
                                body=cache_key_bytes,
                                params=None,
                            )

                            response_body = await response.aread()
                            response_status = response.status_code

                            # If successful, continue with normal flow
                            if response_status < 400:
                                response_json = (
                                    json.loads(response_body.decode()) if response_body else {}
                                )

                                if not response_json or "choices" not in response_json:
                                    raise ValueError(
                                        f"Invalid response format from {provider}: missing 'choices' field"
                                    )

                                normalized_response = adapter.parse_response(response_json)

                                if not normalized_response or "content" not in normalized_response:
                                    raise ValueError(
                                        f"Adapter parse_response returned invalid format: {normalized_response}"
                                    )

                                # Update key pool health on success
                                if selected_key and key_pool_manager:
                                    key_pool_manager.record_success(selected_key.id)
                                    key_pool_requests_total.labels(
                                        provider_key_id=selected_key.id,
                                        provider=selected_key.provider,
                                        status="success",
                                    ).inc()

                                # Calculate cost
                                usage = response_json.get("usage", {})
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", 0)
                                cost_usd = adapter.get_cost_usd(
                                    final_model, prompt_tokens, completion_tokens
                                )

                                result_data = {
                                    "content": normalized_response.get("content", ""),
                                    "role": normalized_response.get("role", "assistant"),
                                    "finish_reason": normalized_response.get(
                                        "finish_reason", "stop"
                                    ),
                                    "usage": {
                                        "prompt_tokens": prompt_tokens,
                                        "completion_tokens": completion_tokens,
                                        "total_tokens": prompt_tokens + completion_tokens,
                                    },
                                }

                                # Store in cache
                                if cache_config.get("enabled", True):
                                    ttl = cache_ttl or cache_config.get("ttl_s", 3600)
                                    cache.set(
                                        "POST",
                                        base_url + api_path,
                                        None,
                                        cache_key_bytes,
                                        {
                                            "body": result_data,
                                            "cost_usd": cost_usd,
                                        },
                                        ttl_s=ttl,
                                        query=None,
                                        allow_post=True,
                                        tenant=tenant,
                                    )

                                # Store idempotency result
                                if idempotency_key:
                                    idempotency_ttl = (
                                        cache_ttl or cache_config.get("ttl_s", 3600)
                                        if cache_config.get("enabled", True)
                                        else 3600
                                    )
                                    idempotency.store_result(
                                        idempotency_key,
                                        {
                                            "data": result_data,
                                            "cost_usd": cost_usd,
                                        },
                                        ttl_s=idempotency_ttl,
                                        tenant=tenant,
                                    )
                                    idempotency.clear_in_progress(idempotency_key, tenant=tenant)

                                duration_ms = int((time.time() - start_time) * 1000)
                                _log_and_metric_llm_request(
                                    request_id=request_id,
                                    target_name=target_name,
                                    provider=provider,
                                    model=final_model,
                                    stream=False,
                                    outcome="success",
                                    latency_ms=duration_ms,
                                    cache_hit=False,
                                    idempotent_hit=False,
                                    cost_usd=cost_usd,
                                    tenant=tenant,
                                )
                                llm_requests_total.labels(
                                    target=target_name, provider=provider, status="success"
                                ).inc()
                                latency_ms.labels(target=target_name, status="success").observe(
                                    duration_ms
                                )

                                return SuccessResponse(
                                    success=True,
                                    data=result_data,
                                    meta=MetaResponse(
                                        target=target_name,
                                        provider=provider,
                                        model=final_model,
                                        cache_hit=False,
                                        idempotent_hit=False,
                                        retries=retries,
                                        duration_ms=duration_ms,
                                        request_id=request_id,
                                        trace_id=None,
                                        cost_usd=cost_usd,
                                        cost_estimate_usd=cost_estimate_usd,
                                        cost_policy_applied=cost_policy_applied,
                                        max_tokens_reduced=max_tokens_reduced
                                        if max_tokens_reduced
                                        else None,
                                        original_max_tokens=original_max_tokens
                                        if max_tokens_reduced
                                        else None,
                                    ),
                                )
                        except Exception:
                            # Fall through to error handling
                            pass

            # No fallback or all fallbacks failed
            if idempotency_key:
                idempotency.clear_in_progress(idempotency_key, tenant=tenant)

            duration_ms = int((time.time() - start_time) * 1000)
            error_code = ErrorCode.from_http_status(response_status)
            upstream_status_norm = UpstreamStatus.normalize(response_status)
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=provider,
                model=final_model,
                stream=False,
                outcome="error",
                latency_ms=duration_ms,
                cache_hit=False,
                idempotent_hit=False,
                error_code=error_code.value,
                upstream_status=response_status,  # Actual status for logs
                tenant=tenant,
            )
            # Legacy metrics
            llm_requests_total.labels(target=target_name, provider=provider, status="error").inc()
            errors_total.labels(
                target=target_name,
                kind="llm",
                error_code=error_code.value,
                upstream_status=upstream_status_norm,
            ).inc()
            latency_ms.labels(target=target_name, status="error").observe(duration_ms)
            return ErrorResponse(
                success=False,
                error=ErrorDetail(
                    type="upstream_error",
                    code=error_code.value,
                    message=f"Upstream returned {response_status}",
                    retryable=response_status >= 500 or response_status == 429,
                    target=target_name,
                    status_code=response_status,
                ),
                meta=MetaResponse(
                    target=target_name,
                    provider=provider,
                    model=final_model,
                    cache_hit=False,
                    retries=retries,
                    duration_ms=duration_ms,
                    request_id=request_id,
                    trace_id=None,
                ),
            )

        # Parse response
        response_json = json.loads(response_body.decode()) if response_body else {}

        # Validate response has required fields
        if not response_json or "choices" not in response_json:
            raise ValueError(f"Invalid response format from {provider}: missing 'choices' field")

        normalized_response = adapter.parse_response(response_json)

        # Validate normalized response
        if not normalized_response or "content" not in normalized_response:
            raise ValueError(
                f"Adapter parse_response returned invalid format: {normalized_response}"
            )

        # Update key pool health on success
        if selected_key and key_pool_manager:
            key_pool_manager.record_success(selected_key.id)
            key_pool_requests_total.labels(
                provider_key_id=selected_key.id,
                provider=selected_key.provider,
                status="success",
            ).inc()
            key_pool_qps.labels(provider_key_id=selected_key.id).observe(selected_key.current_qps)
            status_value = {"active": 0, "degraded": 1, "exhausted": 2, "banned": 3}.get(
                selected_key.status, 0
            )
            key_pool_status.labels(
                provider_key_id=selected_key.id, status=selected_key.status
            ).observe(status_value)

        # Calculate cost
        usage = response_json.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost_usd = adapter.get_cost_usd(final_model, prompt_tokens, completion_tokens)

        result_data = {
            "content": normalized_response.get("content", ""),
            "role": normalized_response.get("role", "assistant"),
            "finish_reason": normalized_response.get("finish_reason", "stop"),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

        # Store in cache
        if cache_config.get("enabled", True):
            ttl = cache_ttl or cache_config.get("ttl_s", 3600)
            cache.set(
                "POST",
                base_url + api_path,
                None,
                cache_key_bytes,
                {
                    "body": result_data,
                    "cost_usd": cost_usd,
                },
                ttl_s=ttl,
                query=None,
                allow_post=True,
                tenant=tenant,
            )

        # Store idempotency result (use same TTL as cache for consistency)
        if idempotency_key:
            idempotency_ttl = (
                cache_ttl or cache_config.get("ttl_s", 3600)
                if cache_config.get("enabled", True)
                else 3600
            )
            idempotency.store_result(
                idempotency_key,
                {
                    "data": result_data,
                    "cost_usd": cost_usd,
                },
                ttl_s=idempotency_ttl,
                tenant=tenant,
            )
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        duration_ms = int((time.time() - start_time) * 1000)
        _log_and_metric_llm_request(
            request_id=request_id,
            target_name=target_name,
            provider=provider,
            model=final_model,
            stream=False,
            outcome="success",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,
            cost_usd=cost_usd,
            tenant=tenant,
        )
        # Legacy metrics
        llm_requests_total.labels(target=target_name, provider=provider, status="success").inc()
        latency_ms.labels(target=target_name, status="success").observe(duration_ms)
        # Note: llm_cost_usd legacy metric removed, using llm_cost_usd_total instead
        return SuccessResponse(
            success=True,
            data=result_data,
            meta=MetaResponse(
                target=target_name,
                provider=provider,
                model=final_model,
                cache_hit=False,
                idempotent_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
                cost_usd=cost_usd,
                cost_estimate_usd=cost_estimate_usd,
                cost_policy_applied=cost_policy_applied,
                max_tokens_reduced=max_tokens_reduced if max_tokens_reduced else None,
                original_max_tokens=original_max_tokens if max_tokens_reduced else None,
            ),
        )

    except httpx.RequestError as e:
        if idempotency_key:
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        # Update key pool health on network error
        if selected_key and key_pool_manager:
            key_pool_manager.record_error(selected_key.id, "network", None)
            key_pool_requests_total.labels(
                provider_key_id=selected_key.id,
                provider=selected_key.provider,
                status="error",
            ).inc()
            key_pool_errors_total.labels(
                provider_key_id=selected_key.id,
                error_type="network",
            ).inc()

        duration_ms = int((time.time() - start_time) * 1000)
        error_code = ErrorCode.NETWORK_ERROR
        upstream_status_norm = UpstreamStatus.BAD_GATEWAY.value
        _log_and_metric_llm_request(
            request_id=request_id,
            target_name=target_name,
            provider=provider,
            model=final_model,
            stream=False,
            outcome="error",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,
            error_code=error_code.value,
            upstream_status=502,  # Actual status for logs
            tenant=tenant,
        )
        # Legacy metrics
        llm_requests_total.labels(target=target_name, provider=provider, status="error").inc()
        errors_total.labels(
            target=target_name,
            kind="llm",
            error_code=error_code.value,
            upstream_status=upstream_status_norm,
        ).inc()
        latency_ms.labels(target=target_name, status="error").observe(duration_ms)
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="upstream_error",
                code=error_code.value,
                message=f"Network error: {str(e)}",
                retryable=True,
                target=target_name,
                status_code=502,
            ),
            meta=MetaResponse(
                target=target_name,
                provider=provider,
                model=final_model,
                cache_hit=False,
                idempotent_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
            ),
        )

    except Exception as e:
        if idempotency_key:
            idempotency.clear_in_progress(idempotency_key, tenant=tenant)

        duration_ms = int((time.time() - start_time) * 1000)
        error_code = ErrorCode.INTERNAL_ERROR
        upstream_status_norm = UpstreamStatus.INTERNAL_SERVER_ERROR.value
        _log_and_metric_llm_request(
            request_id=request_id,
            target_name=target_name,
            provider=provider,
            model=final_model,
            stream=False,
            outcome="error",
            latency_ms=duration_ms,
            cache_hit=False,
            idempotent_hit=False,
            error_code=error_code.value,
            upstream_status=500,  # Actual status for logs
            tenant=tenant,
        )
        # Legacy metrics
        llm_requests_total.labels(target=target_name, provider=provider, status="error").inc()
        errors_total.labels(
            target=target_name,
            kind="llm",
            error_code=error_code.value,
            upstream_status=upstream_status_norm,
        ).inc()
        latency_ms.labels(target=target_name, status="error").observe(duration_ms)
        return ErrorResponse(
            success=False,
            error=ErrorDetail(
                type="internal_error",
                code=error_code.value,
                message=f"Internal error: {str(e)}",
                retryable=True,
                target=target_name,
                status_code=500,
            ),
            meta=MetaResponse(
                target=target_name,
                provider=provider,
                model=final_model,
                cache_hit=False,
                retries=retries,
                duration_ms=duration_ms,
                request_id=request_id,
                trace_id=None,
            ),
        )

    finally:
        await client.close()


async def handle_llm_stream_generator(
    target_name: str,
    messages: List[Dict[str, str]],
    model: Optional[str],
    max_tokens: Optional[int],
    temperature: Optional[float],
    top_p: Optional[float],
    stop: Optional[List[str]],
    idempotency_key: Optional[str],
    cache_ttl: Optional[int],
    targets: Dict[str, Dict],
    cache: Cache,
    idempotency: IdempotencyManager,
    request_id: str,
    tenant: Optional[str] = None,
    tier: str = "free",
) -> AsyncIterator[str]:
    """Handle LLM streaming request - yields SSE events."""
    import json

    start_time = time.time()
    stream_started = False

    try:
        # Get target config
        target_config = targets.get(target_name)
        if not target_config:
            error_data = {
                "code": "NOT_FOUND",
                "message": f"Target '{target_name}' not found",
                "upstream_status": 404,
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
            return

        # Check if target has LLM config
        llm_config = target_config.get("llm", {})
        if not llm_config:
            error_data = {
                "code": "INVALID_TARGET",
                "message": f"Target '{target_name}' is not configured for LLM",
                "upstream_status": 400,
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
            return

        # Apply config limits
        final_model = model or llm_config.get("default_model", "gpt-4")
        final_max_tokens = max_tokens
        if final_max_tokens is None:
            final_max_tokens = llm_config.get("max_tokens")
        elif llm_config.get("max_tokens"):
            final_max_tokens = min(final_max_tokens, llm_config["max_tokens"])

        final_temperature = temperature
        if final_temperature is None:
            final_temperature = llm_config.get("temperature")
        elif llm_config.get("temperature") is not None:
            final_temperature = min(final_temperature, llm_config["temperature"])

        # Get provider
        base_url = target_config["base_url"]
        provider = llm_config.get("provider") or detect_provider(base_url)

        if not provider:
            error_code_enum = ErrorCode.UNKNOWN_PROVIDER
            upstream_status_norm = UpstreamStatus.INTERNAL_SERVER_ERROR.value
            error_data = {
                "code": error_code_enum.value,
                "message": f"Could not determine provider for target '{target_name}'",
                "upstream_status": 500,
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
            # Log and metric
            duration_ms = int((time.time() - start_time) * 1000)
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=None,
                model=final_model,
                stream=True,
                outcome="error",
                latency_ms=duration_ms,
                cache_hit=False,
                idempotent_hit=False,
                error_code=error_code_enum.value,
                upstream_status=500,
                tenant=tenant,
            )
            return

        adapter = get_adapter(provider)
        if not adapter:
            error_code_enum = ErrorCode.ADAPTER_NOT_FOUND
            upstream_status_norm = UpstreamStatus.INTERNAL_SERVER_ERROR.value
            error_data = {
                "code": error_code_enum.value,
                "message": f"Adapter not found for provider '{provider}'",
                "upstream_status": 500,
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
            # Log and metric
            duration_ms = int((time.time() - start_time) * 1000)
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=provider,
                model=final_model,
                stream=True,
                outcome="error",
                latency_ms=duration_ms,
                cache_hit=False,
                idempotent_hit=False,
                error_code=error_code_enum.value,
                upstream_status=500,
                tenant=tenant,
            )
            return

        # Check streaming support
        if not adapter.supports_streaming():
            error_code_enum = ErrorCode.STREAMING_UNSUPPORTED
            upstream_status_norm = UpstreamStatus.BAD_REQUEST.value
            error_data = {
                "code": error_code_enum.value,
                "message": f"Provider '{provider}' does not support streaming",
                "upstream_status": 400,
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
            # Log and metric
            duration_ms = int((time.time() - start_time) * 1000)
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=provider,
                model=final_model,
                stream=True,
                outcome="error",
                latency_ms=duration_ms,
                cache_hit=False,
                idempotent_hit=False,
                error_code=error_code_enum.value,
                upstream_status=400,
                tenant=tenant,
            )
            return

        # Budget control: estimate cost and check caps
        cost_estimate_usd = None
        cost_policy_applied = "none"
        max_tokens_reduced = False
        original_max_tokens = None

        cost_estimate_usd = CostEstimator.estimate_from_messages(
            provider, final_model, messages, final_max_tokens
        )

        # Check hard cost cap (reject if exceeded)
        hard_cost_cap = llm_config.get("hard_cost_cap_usd")
        if hard_cost_cap and cost_estimate_usd and cost_estimate_usd > hard_cost_cap:
            duration_ms = int((time.time() - start_time) * 1000)
            budget_events_total.labels(
                target=target_name, event="hard_cap", tenant=tenant or "default"
            ).inc()
            error_code = ErrorCode.BUDGET_EXCEEDED
            upstream_status_norm = UpstreamStatus.BAD_REQUEST.value
            _log_and_metric_llm_request(
                request_id=request_id,
                target_name=target_name,
                provider=provider,
                model=final_model,
                stream=True,
                outcome="error",
                latency_ms=duration_ms,
                cache_hit=False,
                idempotent_hit=False,
                error_code=error_code.value,
                upstream_status=400,
                tenant=tenant,
            )
            error_data = {
                "code": error_code.value,
                "message": f"Estimated cost ${cost_estimate_usd:.6f} exceeds hard cap ${hard_cost_cap:.6f}",
                "upstream_status": 400,
                "details": {
                    "cost_estimate_usd": cost_estimate_usd,
                    "hard_cost_cap_usd": hard_cost_cap,
                },
            }
            yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
            return

        # Check soft cost cap (throttle by reducing max_tokens)
        soft_cost_cap = llm_config.get("soft_cost_cap_usd")
        if soft_cost_cap and cost_estimate_usd and cost_estimate_usd > soft_cost_cap:
            reduction_factor = soft_cost_cap / cost_estimate_usd
            original_max_tokens = final_max_tokens
            final_max_tokens = int(
                final_max_tokens * reduction_factor * 0.9
            )  # 0.9 for safety margin
            max_tokens_reduced = True
            cost_policy_applied = "soft_cap_throttled"
            budget_events_total.labels(
                target=target_name, event="soft_cap", tenant=tenant or "default"
            ).inc()

            # Re-estimate with reduced tokens
            cost_estimate_usd = CostEstimator.estimate_from_messages(
                provider, final_model, messages, final_max_tokens
            )

        # Handle idempotency for streaming (MVP: simple check)
        if idempotency_key:
            full_url = f"{base_url}/chat/completions"  # Simplified path
            cache_key_bytes = json.dumps(
                {
                    "messages": messages,
                    "model": final_model,
                    "max_tokens": final_max_tokens,
                },
                sort_keys=True,
            ).encode()

            is_new, existing_id, existing_hash = idempotency.register_request(
                idempotency_key, "POST", full_url, None, cache_key_bytes, request_id, tenant=tenant
            )

            if not is_new:
                # Check if request differs
                current_hash = idempotency.make_request_hash(
                    "POST", full_url, None, cache_key_bytes
                )
                if existing_hash != current_hash:
                    error_data = {
                        "code": "IDEMPOTENCY_CONFLICT",
                        "message": f"Idempotency key '{idempotency_key}' used with different request",
                        "upstream_status": 409,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
                    return

                # Check if result exists (completed stream)
                existing_result = idempotency.get_result(idempotency_key, tenant=tenant)
                if existing_result:
                    # For MVP: return cached result as non-stream JSON
                    # In future: could simulate SSE stream
                    error_data = {
                        "code": ErrorCode.STREAM_ALREADY_COMPLETED.value,
                        "message": f"Stream already completed for idempotency key '{idempotency_key}'. Use non-streaming request to get cached result.",
                        "upstream_status": 409,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
                    return

                # Check if stream is in progress
                if idempotency.is_in_progress(idempotency_key, tenant=tenant):
                    error_data = {
                        "code": ErrorCode.STREAM_ALREADY_IN_PROGRESS.value,
                        "message": f"Stream already in progress for idempotency key '{idempotency_key}'",
                        "upstream_status": 409,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
                    return

            idempotency.mark_in_progress(idempotency_key, tenant=tenant)

        # Send meta event
        meta_data = {
            "target": target_name,
            "provider": provider,
            "model": final_model,
            "request_id": request_id,
            "cost_estimate_usd": cost_estimate_usd,
            "cost_policy_applied": cost_policy_applied,
            "max_tokens_reduced": max_tokens_reduced if max_tokens_reduced else None,
            "original_max_tokens": original_max_tokens if max_tokens_reduced else None,
        }
        yield f"event: meta\ndata: {json.dumps(meta_data)}\n\n"

        # Prepare request payload
        payload = adapter.prepare_request(
            messages=messages,
            model=final_model,
            max_tokens=final_max_tokens,
            temperature=final_temperature,
            top_p=top_p,
            stop=stop,
            stream=True,
        )

        # Determine API endpoint
        if provider == "openai":
            api_path = "/chat/completions"
        elif provider == "anthropic":
            api_path = "/messages"
        elif provider == "mistral":
            api_path = "/chat/completions"
        else:
            api_path = "/chat/completions"

        # Create HTTP client with auth
        auth_config = target_config.get("auth", {})
        headers = {"Content-Type": "application/json"}
        if auth_config.get("type") == "bearer_env":
            import os

            env_var = auth_config.get("env_var")
            if env_var:
                api_key = os.getenv(env_var)
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"

        # Create httpx client for streaming
        timeout_s = target_config.get("timeout_ms", 20000) / 1000.0
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            try:
                # Stream from provider
                accumulated_content = ""
                finish_reason = None
                prompt_tokens = 0
                completion_tokens = 0

                async for chunk in adapter.stream_chat(
                    client, base_url, api_path, payload, headers
                ):
                    stream_started = True

                    # Parse OpenAI chunk format
                    if provider == "openai":
                        # Check if this is a usage-only chunk (sent after [DONE])
                        if chunk.get("_usage_only"):
                            usage = chunk.get("usage", {})
                            if usage:
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", 0)
                            continue

                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content_delta = delta.get("content", "")
                            if content_delta:
                                accumulated_content += content_delta
                                yield f"event: chunk\ndata: {json.dumps({'delta': content_delta, 'finish_reason': None})}\n\n"

                            # Check for finish reason
                            if choices[0].get("finish_reason"):
                                finish_reason = choices[0]["finish_reason"]

                        # Get usage if available in regular chunk (some providers include it)
                        usage = chunk.get("usage", {})
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", 0)

                    # Parse Anthropic chunk format
                    elif provider == "anthropic":
                        # Check if this is a usage-only chunk
                        if chunk.get("_usage_only"):
                            usage = chunk.get("usage", {})
                            if usage:
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", 0)
                            continue

                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content_delta = delta.get("content", "")
                            if content_delta:
                                accumulated_content += content_delta
                                yield f"event: chunk\ndata: {json.dumps({'delta': content_delta, 'finish_reason': None})}\n\n"

                            # Check for finish reason
                            if choices[0].get("finish_reason"):
                                finish_reason = choices[0]["finish_reason"]

                        # Get usage if available
                        usage = chunk.get("usage", {})
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", 0)

                    # Parse Mistral chunk format (similar to OpenAI)
                    elif provider == "mistral":
                        # Check if this is a usage-only chunk
                        if chunk.get("_usage_only"):
                            usage = chunk.get("usage", {})
                            if usage:
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", 0)
                            continue

                        choices = chunk.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content_delta = delta.get("content", "")
                            if content_delta:
                                accumulated_content += content_delta
                                yield f"event: chunk\ndata: {json.dumps({'delta': content_delta, 'finish_reason': None})}\n\n"

                            # Check for finish reason
                            if choices[0].get("finish_reason"):
                                finish_reason = choices[0]["finish_reason"]

                        # Get usage if available
                        usage = chunk.get("usage", {})
                        if usage:
                            prompt_tokens = usage.get("prompt_tokens", 0)
                            completion_tokens = usage.get("completion_tokens", 0)

                # Calculate final cost
                cost_usd = adapter.get_cost_usd(final_model, prompt_tokens, completion_tokens)

                # Send done event
                done_data = {
                    "finish_reason": finish_reason or "stop",
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                    "cost_usd": cost_usd,
                }
                yield f"event: done\ndata: {json.dumps(done_data)}\n\n"

                # Store in cache and idempotency (final completion only)
                cache_config = target_config.get("cache", {})
                if cache_config.get("enabled", True):
                    ttl = cache_ttl or cache_config.get("ttl_s", 3600)
                    result_data = {
                        "content": accumulated_content,
                        "role": "assistant",
                        "finish_reason": finish_reason or "stop",
                        "usage": done_data["usage"],
                    }
                    cache.set(
                        "POST",
                        base_url + api_path,
                        None,
                        json.dumps(payload, sort_keys=True).encode(),
                        {
                            "body": result_data,
                            "cost_usd": cost_usd,
                        },
                        ttl_s=ttl,
                        query=None,
                        allow_post=True,
                        tenant=tenant,
                    )

                if idempotency_key:
                    idempotency_ttl = (
                        cache_ttl or cache_config.get("ttl_s", 3600)
                        if cache_config.get("enabled", True)
                        else 3600
                    )
                    idempotency.store_result(
                        idempotency_key,
                        {
                            "data": {
                                "content": accumulated_content,
                                "role": "assistant",
                                "finish_reason": finish_reason or "stop",
                                "usage": done_data["usage"],
                            },
                            "cost_usd": cost_usd,
                        },
                        ttl_s=idempotency_ttl,
                        tenant=tenant,
                    )
                    idempotency.clear_in_progress(idempotency_key, tenant=tenant)

                # Update metrics and log
                duration_ms = int((time.time() - start_time) * 1000)
                _log_and_metric_llm_request(
                    request_id=request_id,
                    target_name=target_name,
                    provider=provider,
                    model=final_model,
                    stream=True,
                    outcome="success",
                    latency_ms=duration_ms,
                    cache_hit=False,
                    idempotent_hit=False,
                    cost_usd=cost_usd,
                    tenant=tenant,
                )
                # Legacy metrics
                llm_requests_total.labels(
                    target=target_name, provider=provider, status="success"
                ).inc()
                latency_ms.labels(target=target_name, status="success").observe(duration_ms)
                # Note: llm_cost_usd legacy metric removed, using llm_cost_usd_total instead

            except httpx.HTTPStatusError as e:
                if stream_started:
                    # Stream already started, send error event
                    error_code_enum = ErrorCode.UPSTREAM_STREAM_INTERRUPTED
                    upstream_status_norm = UpstreamStatus.normalize(e.response.status_code)
                    error_data = {
                        "code": error_code_enum.value,
                        "message": f"Upstream stream interrupted: {e.response.status_code}",
                        "upstream_status": e.response.status_code,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
                else:
                    # Stream not started yet, can retry/fallback (simplified for MVP)
                    error_code_enum = ErrorCode.from_http_status(e.response.status_code)
                    upstream_status_norm = UpstreamStatus.normalize(e.response.status_code)
                    error_data = {
                        "code": error_code_enum.value,
                        "message": f"Upstream returned {e.response.status_code}",
                        "upstream_status": e.response.status_code,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"

                if idempotency_key:
                    idempotency.clear_in_progress(idempotency_key, tenant=tenant)

                duration_ms = int((time.time() - start_time) * 1000)
                _log_and_metric_llm_request(
                    request_id=request_id,
                    target_name=target_name,
                    provider=provider,
                    model=final_model,
                    stream=True,
                    outcome="error",
                    latency_ms=duration_ms,
                    cache_hit=False,
                    idempotent_hit=False,
                    error_code=error_code_enum.value,
                    upstream_status=e.response.status_code,  # Actual status for logs
                    tenant=tenant,
                )
                # Legacy metrics
                llm_requests_total.labels(
                    target=target_name, provider=provider, status="error"
                ).inc()
                errors_total.labels(
                    target=target_name,
                    kind="llm",
                    error_code=error_code_enum.value,
                    upstream_status=upstream_status_norm,
                ).inc()
                latency_ms.labels(target=target_name, status="error").observe(duration_ms)

            except httpx.RequestError as e:
                if stream_started:
                    error_code_enum = ErrorCode.UPSTREAM_STREAM_INTERRUPTED
                    upstream_status_norm = UpstreamStatus.BAD_GATEWAY.value
                    error_data = {
                        "code": error_code_enum.value,
                        "message": f"Network error during stream: {str(e)}",
                        "upstream_status": 502,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
                else:
                    error_code_enum = ErrorCode.NETWORK_ERROR
                    upstream_status_norm = UpstreamStatus.BAD_GATEWAY.value
                    error_data = {
                        "code": error_code_enum.value,
                        "message": f"Network error: {str(e)}",
                        "upstream_status": 502,
                    }
                    yield f"event: error\ndata: {json.dumps(error_data)}\n\n"

                if idempotency_key:
                    idempotency.clear_in_progress(idempotency_key, tenant=tenant)

                duration_ms = int((time.time() - start_time) * 1000)
                _log_and_metric_llm_request(
                    request_id=request_id,
                    target_name=target_name,
                    provider=provider,
                    model=final_model,
                    stream=True,
                    outcome="error",
                    latency_ms=duration_ms,
                    cache_hit=False,
                    idempotent_hit=False,
                    error_code=error_code_enum.value,
                    upstream_status=502,  # Actual status for logs
                    tenant=tenant,
                )
                # Legacy metrics
                llm_requests_total.labels(
                    target=target_name, provider=provider, status="error"
                ).inc()
                errors_total.labels(
                    target=target_name,
                    kind="llm",
                    error_code=error_code_enum.value,
                    upstream_status=upstream_status_norm,
                ).inc()
                latency_ms.labels(target=target_name, status="error").observe(duration_ms)

            except Exception as e:
                error_code_enum = ErrorCode.INTERNAL_ERROR
                upstream_status_norm = UpstreamStatus.INTERNAL_SERVER_ERROR.value
                error_data = {
                    "code": error_code_enum.value,
                    "message": f"Internal error: {str(e)}",
                    "upstream_status": 500,
                }
                yield f"event: error\ndata: {json.dumps(error_data)}\n\n"

                if idempotency_key:
                    idempotency.clear_in_progress(idempotency_key, tenant=tenant)

                duration_ms = int((time.time() - start_time) * 1000)
                _log_and_metric_llm_request(
                    request_id=request_id,
                    target_name=target_name,
                    provider=provider,
                    model=final_model,
                    stream=True,
                    outcome="error",
                    latency_ms=duration_ms,
                    cache_hit=False,
                    idempotent_hit=False,
                    error_code=error_code_enum.value,
                    upstream_status=500,  # Actual status for logs
                    tenant=tenant,
                )
                # Legacy metrics
                llm_requests_total.labels(
                    target=target_name, provider=provider, status="error"
                ).inc()
                errors_total.labels(
                    target=target_name,
                    kind="llm",
                    error_code=error_code_enum.value,
                    upstream_status=upstream_status_norm,
                ).inc()
                latency_ms.labels(target=target_name, status="error").observe(duration_ms)

    except Exception as e:
        # Catch-all for any errors before stream starts
        error_code_enum = ErrorCode.INTERNAL_ERROR
        upstream_status_norm = UpstreamStatus.INTERNAL_SERVER_ERROR.value
        error_data = {
            "code": error_code_enum.value,
            "message": f"Internal error: {str(e)}",
            "upstream_status": 500,
        }
        yield f"event: error\ndata: {json.dumps(error_data)}\n\n"
