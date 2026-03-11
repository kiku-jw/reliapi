"""Proxy endpoints for HTTP and LLM requests.

This module provides:
- POST /proxy/http - Universal HTTP proxy with reliability features
- POST /proxy/llm - LLM proxy with idempotency and budget control
"""
import logging
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from reliapi.app.dependencies import (
    detect_client_profile,
    get_account_id,
    get_app_state,
    verify_api_key,
)
from reliapi.app.schemas import HTTPProxyRequest, LLMProxyRequest
from reliapi.app.services import (
    handle_http_proxy,
    handle_llm_proxy,
    handle_llm_stream_generator,
)
from reliapi.core.free_tier_restrictions import FreeTierRestrictions
from reliapi.core.security import SecurityManager
from reliapi.integrations.routellm import (
    apply_routellm_overrides,
    extract_routellm_decision,
    routellm_metrics,
)
from reliapi.metrics.prometheus import (
    free_tier_abuse_attempts_total,
    rapidapi_tier_distribution,
    routellm_decisions_total,
    routellm_overrides_total,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Proxy"])


def _check_api_key_format(api_key: Optional[str]) -> None:
    """Validate API key format and raise HTTPException if invalid."""
    if api_key:
        is_valid, error_msg = SecurityManager.validate_api_key_format(api_key)
        if not is_valid:
            raise HTTPException(
                status_code=400,
                detail={
                    "type": "client_error",
                    "code": "INVALID_API_KEY_FORMAT",
                    "message": error_msg or "Invalid API key format",
                },
            )


def _check_free_tier_rate_limits(
    request: Request,
    api_key: Optional[str],
    tier: str,
    endpoint: str = "http",
) -> None:
    """Check rate limits and abuse protection for Free tier.

    Args:
        request: FastAPI request
        api_key: API key
        tier: User tier
        endpoint: Endpoint type ('http' or 'llm')

    Raises:
        HTTPException: If rate limit exceeded or abuse detected
    """
    state = get_app_state()

    if not state.rate_limiter or tier != "free":
        return

    state.rate_limiter._current_tier = tier
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "")
    account_id = get_account_id(api_key)

    # Check IP rate limit (20 req/min)
    allowed, error = state.rate_limiter.check_ip_rate_limit(
        client_ip, limit_per_minute=20
    )
    if not allowed:
        free_tier_abuse_attempts_total.labels(
            abuse_type="rate_limit_bypass", tier=tier
        ).inc()
        logger.warning(
            f"Free tier abuse attempt: IP rate limit exceeded for "
            f"tier={tier}, IP={client_ip}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "rate_limit_error",
                "code": error,
                "message": "Rate limit exceeded. Free tier: 20 requests/minute per IP.",
            },
        )

    # Check account burst limit (500 req/min)
    allowed, error = state.rate_limiter.check_account_burst_limit(
        account_id, limit_per_minute=500
    )
    if not allowed:
        free_tier_abuse_attempts_total.labels(
            abuse_type="burst_limit", tier=tier
        ).inc()
        logger.warning(
            f"Free tier abuse attempt: burst limit exceeded for "
            f"tier={tier}, account_id={account_id}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "abuse_error",
                "code": error,
                "message": "Burst limit exceeded. Free tier abuse detected.",
            },
        )

    # Check fingerprint limit
    allowed, error = state.rate_limiter.check_fingerprint_limit(
        client_ip, user_agent, api_key or "", limit_per_minute=20
    )
    if not allowed:
        free_tier_abuse_attempts_total.labels(
            abuse_type="fingerprint_mismatch", tier=tier
        ).inc()
        logger.warning(
            f"Free tier abuse attempt: fingerprint mismatch for "
            f"tier={tier}, account_id={account_id}"
        )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "rate_limit_error",
                "code": error,
                "message": "Rate limit exceeded based on fingerprint.",
            },
        )

    # Check anomaly detector
    allowed, error = state.rate_limiter.check_anomaly_detector(account_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "type": "anomaly_error",
                "code": error,
                "message": "Anomalous activity detected. Request throttled.",
            },
        )


def _check_llm_free_tier_restrictions(
    request: Request,
    llm_request: LLMProxyRequest,
    api_key: Optional[str],
    tier: str,
) -> None:
    """Check LLM-specific Free tier restrictions.

    Args:
        request: FastAPI request
        llm_request: LLM proxy request
        api_key: API key
        tier: User tier

    Raises:
        HTTPException: If restriction violated
    """
    state = get_app_state()

    # Block SSE streaming for free tier
    if tier == "free" and llm_request.stream:
        allowed, error = FreeTierRestrictions.is_feature_allowed("streaming", tier)
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "type": "feature_error",
                    "code": error,
                    "message": "SSE streaming not available for Free tier.",
                },
            )

    if not state.rate_limiter or tier != "free":
        return

    state.rate_limiter._current_tier = tier
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("User-Agent", "")
    accept_language = request.headers.get("Accept-Language", "")
    account_id = get_account_id(api_key)

    # Check auto-ban first (>5 bypass attempts)
    should_ban, ban_reason = state.rate_limiter.check_auto_ban(
        account_id, client_ip, max_attempts=5
    )
    if should_ban:
        free_tier_abuse_attempts_total.labels(abuse_type="auto_ban", tier=tier).inc()
        logger.warning(
            f"Free tier abuse: account/IP banned for tier={tier}, "
            f"account_id={account_id}, reason={ban_reason}"
        )
        raise HTTPException(
            status_code=403,
            detail={
                "type": "abuse_error",
                "code": "ACCOUNT_BANNED",
                "message": f"Account/IP banned: {ban_reason}",
            },
        )

    # Check IP rate limit (20 req/min)
    allowed, error = state.rate_limiter.check_ip_rate_limit(
        client_ip, limit_per_minute=20
    )
    if not allowed:
        state.rate_limiter.abuse_detector.record_limit_bypass_attempt(
            account_id, client_ip
        )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "rate_limit_error",
                "code": error,
                "message": "Rate limit exceeded. Free tier: 20 requests/minute per IP.",
            },
        )

    # Check burst protection (≤300 req/10min)
    allowed, error = state.rate_limiter.check_burst_protection(
        account_id, limit_per_10min=300
    )
    if not allowed:
        state.rate_limiter.abuse_detector.record_limit_bypass_attempt(
            account_id, client_ip
        )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "abuse_error",
                "code": error,
                "message": "Burst limit exceeded. Free tier: maximum 300 requests per 10 minutes.",
            },
        )

    # Check account burst limit (500 req/min)
    allowed, error = state.rate_limiter.check_account_burst_limit(
        account_id, limit_per_minute=500
    )
    if not allowed:
        state.rate_limiter.abuse_detector.record_limit_bypass_attempt(
            account_id, client_ip
        )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "abuse_error",
                "code": error,
                "message": "Burst limit exceeded. Free tier abuse detected.",
            },
        )

    # Check usage anomaly (3x average)
    allowed, error = state.rate_limiter.check_usage_anomaly(
        account_id, multiplier=3.0
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "type": "anomaly_error",
                "code": error,
                "message": "Usage anomaly detected. Request throttled.",
            },
        )

    # Check fingerprint-based identity
    allowed, error = state.rate_limiter.check_fingerprint(
        account_id, client_ip, user_agent, accept_language
    )
    if not allowed:
        if error == "FINGERPRINT_MISMATCH_BANNED":
            raise HTTPException(
                status_code=403,
                detail={
                    "type": "abuse_error",
                    "code": error,
                    "message": "Account banned due to multiple fingerprint mismatches.",
                },
            )
        raise HTTPException(
            status_code=429,
            detail={
                "type": "abuse_error",
                "code": error,
                "message": "Fingerprint mismatch detected. Request throttled.",
            },
        )

    # Validate model restrictions
    if llm_request.model:
        allowed, error = FreeTierRestrictions.is_model_allowed(
            llm_request.target,
            llm_request.model,
            tier,
        )
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "type": "feature_error",
                    "code": error,
                    "message": (
                        f"Model {llm_request.model} not allowed for Free tier. "
                        "Allowed: gpt-4o-mini, claude-3-haiku, mistral-small"
                    ),
                },
            )

    # Check idempotency restriction
    if llm_request.idempotency_key:
        allowed, error = FreeTierRestrictions.is_feature_allowed("idempotency", tier)
        if not allowed:
            raise HTTPException(
                status_code=403,
                detail={
                    "type": "feature_error",
                    "code": error,
                    "message": "Idempotency not available for Free tier.",
                },
            )


@router.post(
    "/proxy/http",
    summary="Proxy HTTP request",
    description=(
        "Universal HTTP proxy endpoint for any HTTP API. "
        "Supports retries, circuit breaker, cache, and idempotency. "
        "Use this endpoint to add reliability layers to any HTTP API call."
    ),
)
async def proxy_http(
    request: HTTPProxyRequest,
    http_request: Request,
) -> JSONResponse:
    """Universal HTTP proxy endpoint for any HTTP API."""
    state = get_app_state()

    # Verify API key and resolve tenant/tier
    api_key, tenant, tier = verify_api_key(http_request)

    # Validate API key format
    _check_api_key_format(api_key)

    # Check rate limits for free tier
    _check_free_tier_rate_limits(http_request, api_key, tier, endpoint="http")

    # Generate request ID
    request_id = f"req_{uuid.uuid4().hex[:16]}"

    # Detect client profile
    client_profile_name = detect_client_profile(http_request, tenant=tenant)

    result = await handle_http_proxy(
        target_name=request.target,
        method=request.method,
        path=request.path,
        headers=request.headers,
        query=request.query,
        body=request.body,
        idempotency_key=request.idempotency_key,
        cache_ttl=request.cache,
        targets=state.targets,
        cache=state.cache,
        idempotency=state.idempotency,
        key_pool_manager=state.key_pool_manager,
        rate_scheduler=state.rate_scheduler,
        client_profile_name=client_profile_name,
        client_profile_manager=state.client_profile_manager,
        request_id=request_id,
        tenant=tenant,
        tier=tier,
    )

    # Record usage for RapidAPI tracking
    if state.rapidapi_client and api_key:
        await state.rapidapi_client.record_usage(
            api_key=api_key,
            endpoint="/proxy/http",
            latency_ms=result.meta.duration_ms,
            status="success" if result.success else "error",
        )
        rapidapi_tier_distribution.labels(tier=tier).inc()

    status_code = 200 if result.success else (result.error.status_code or 500)
    return JSONResponse(
        content=result.model_dump(),
        status_code=status_code,
        headers={
            "X-Request-ID": request_id,
            "X-Cache-Hit": str(result.meta.cache_hit).lower(),
            "X-Retries": str(result.meta.retries),
            "X-Duration-MS": str(result.meta.duration_ms),
        },
    )


@router.post(
    "/proxy/llm",
    summary="Proxy LLM request",
    description=(
        "LLM proxy endpoint with idempotency, budget caps, and caching. "
        "Make idempotent LLM API calls with predictable costs. "
        "Supports OpenAI, Anthropic, and Mistral providers. "
        "Set stream=true for Server-Sent Events (SSE) streaming."
    ),
)
async def proxy_llm(
    request: LLMProxyRequest,
    http_request: Request,
):
    """LLM proxy endpoint with idempotency and budget control."""
    state = get_app_state()

    # Verify API key and resolve tenant/tier
    api_key, tenant, tier = verify_api_key(http_request)

    # Validate API key format
    _check_api_key_format(api_key)

    # Check LLM-specific free tier restrictions
    _check_llm_free_tier_restrictions(http_request, request, api_key, tier)

    # Generate request ID
    request_id = f"req_{uuid.uuid4().hex[:16]}"

    # Extract RouteLLM routing decision from headers
    routellm_decision = extract_routellm_decision(dict(http_request.headers))

    # Apply RouteLLM overrides to target and model
    resolved_target = request.target
    resolved_model = request.model
    if routellm_decision and routellm_decision.has_override:
        resolved_target, resolved_model = apply_routellm_overrides(
            request.target,
            request.model,
            state.targets,
            routellm_decision,
        )

        # Record metrics for RouteLLM routing
        routellm_decisions_total.labels(
            route_name=routellm_decision.route_name or "unknown",
            provider=routellm_decision.provider or "default",
            model=routellm_decision.model or "default",
        ).inc()

        if routellm_decision.provider and routellm_decision.model:
            routellm_overrides_total.labels(override_type="both").inc()
        elif routellm_decision.provider:
            routellm_overrides_total.labels(override_type="provider").inc()
        elif routellm_decision.model:
            routellm_overrides_total.labels(override_type="model").inc()

        routellm_metrics.record_decision(routellm_decision)

    # Handle streaming requests
    if request.stream:
        generator = handle_llm_stream_generator(
            target_name=resolved_target,
            messages=request.messages,
            model=resolved_model,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            top_p=request.top_p,
            stop=request.stop,
            idempotency_key=request.idempotency_key,
            cache_ttl=request.cache,
            targets=state.targets,
            cache=state.cache,
            idempotency=state.idempotency,
            request_id=request_id,
            tenant=tenant,
            tier=tier,
        )

        # Build response headers including RouteLLM correlation
        response_headers: Dict[str, str] = {
            "X-Request-ID": request_id,
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
        if routellm_decision:
            response_headers.update(routellm_decision.to_response_headers())

        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers=response_headers,
        )

    # Detect client profile
    client_profile_name = detect_client_profile(http_request, tenant=tenant)

    # Handle non-streaming requests
    result = await handle_llm_proxy(
        target_name=resolved_target,
        messages=request.messages,
        model=resolved_model,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        top_p=request.top_p,
        stop=request.stop,
        stream=False,
        idempotency_key=request.idempotency_key,
        cache_ttl=request.cache,
        targets=state.targets,
        cache=state.cache,
        idempotency=state.idempotency,
        request_id=request_id,
        tenant=tenant,
        tier=tier,
        key_pool_manager=state.key_pool_manager,
        rate_scheduler=state.rate_scheduler,
        client_profile_name=client_profile_name,
        client_profile_manager=state.client_profile_manager,
    )

    # Record usage for RapidAPI tracking
    if state.rapidapi_client and api_key:
        cost_usd = (
            result.data.usage.estimated_cost_usd
            if result.success and result.data and result.data.usage
            else 0.0
        )
        await state.rapidapi_client.record_usage(
            api_key=api_key,
            endpoint="/proxy/llm",
            latency_ms=result.meta.duration_ms,
            status="success" if result.success else "error",
            cost_usd=cost_usd,
        )
        rapidapi_tier_distribution.labels(tier=tier).inc()

    # Add RouteLLM correlation to response meta
    if routellm_decision:
        result.meta.routellm_decision_id = routellm_decision.decision_id
        result.meta.routellm_route_name = routellm_decision.route_name
        result.meta.routellm_provider_override = routellm_decision.provider
        result.meta.routellm_model_override = routellm_decision.model

    # Build response headers including RouteLLM correlation
    response_headers: Dict[str, str] = {
        "X-Request-ID": request_id,
        "X-Cache-Hit": str(result.meta.cache_hit).lower(),
        "X-Retries": str(result.meta.retries),
        "X-Duration-MS": str(result.meta.duration_ms),
    }
    if routellm_decision:
        response_headers.update(routellm_decision.to_response_headers())

    status_code = 200 if result.success else (result.error.status_code or 500)
    return JSONResponse(
        content=result.model_dump(),
        status_code=status_code,
        headers=response_headers,
    )
