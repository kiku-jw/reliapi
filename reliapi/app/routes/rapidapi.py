"""RapidAPI integration endpoints.

This module provides:
- POST /webhooks/rapidapi - RapidAPI webhook handler
- GET /rapidapi/status - RapidAPI integration status
"""
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from reliapi.app.dependencies import get_app_state, verify_api_key
from reliapi.integrations.rapidapi import SubscriptionTier
from reliapi.metrics.prometheus import (
    rapidapi_tier_cache_total,
    rapidapi_tier_distribution,
    rapidapi_webhook_events_total,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["RapidAPI"])


@router.post(
    "/webhooks/rapidapi",
    summary="RapidAPI Webhook",
    description="Webhook endpoint for RapidAPI events (subscription changes, usage alerts).",
    include_in_schema=False,
)
async def rapidapi_webhook(request: Request) -> JSONResponse:
    """Handle RapidAPI webhook events.

    Supported events:
    - subscription.created: New subscription created
    - subscription.updated: Subscription tier changed
    - subscription.cancelled: Subscription cancelled
    - usage.alert: Usage threshold reached
    """
    state = get_app_state()

    if not state.rapidapi_client:
        raise HTTPException(
            status_code=503,
            detail="RapidAPI integration not configured",
        )

    # Rate limiting for webhook endpoint (IP-based, 10 req/min)
    if state.rate_limiter:
        client_ip = request.client.host if request.client else "unknown"

        allowed, error = state.rate_limiter.check_ip_rate_limit(
            client_ip, limit_per_minute=10, prefix="webhook"
        )
        if not allowed:
            logger.warning(f"Webhook rate limit exceeded for IP: {client_ip}")
            rapidapi_webhook_events_total.labels(
                event_type="unknown", status="rate_limited"
            ).inc()
            raise HTTPException(
                status_code=429,
                detail="Webhook rate limit exceeded (10 requests/minute)",
            )

    # Request size limit (10KB)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 10240:
        logger.warning(f"Webhook payload too large: {content_length} bytes")
        rapidapi_webhook_events_total.labels(
            event_type="unknown", status="payload_too_large"
        ).inc()
        raise HTTPException(
            status_code=413,
            detail="Webhook payload too large (max 10KB)",
        )

    # Get raw body for signature verification
    body = await request.body()

    # Verify webhook signature
    signature = request.headers.get("X-RapidAPI-Signature", "")
    if not state.rapidapi_client.verify_webhook_signature(body, signature):
        logger.warning("Invalid RapidAPI webhook signature")
        rapidapi_webhook_events_total.labels(
            event_type="unknown", status="invalid_signature"
        ).inc()
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse webhook payload
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        logger.error("Invalid JSON in webhook payload")
        rapidapi_webhook_events_total.labels(
            event_type="unknown", status="invalid_json"
        ).inc()
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("type", "unknown")
    event_data = payload.get("data", {})
    event_id = payload.get("id") or payload.get("event_id") or ""

    logger.info(f"Received RapidAPI webhook: {event_type}, event_id={event_id}")

    # Idempotency check
    webhook_idempotency_key = None
    if state.idempotency and event_id:
        webhook_idempotency_key = f"webhook:rapidapi:{event_type}:{event_id}"

        existing_result = state.idempotency.get_result(webhook_idempotency_key)
        if existing_result:
            logger.info(
                f"Duplicate webhook detected: {event_type}, event_id={event_id}"
            )
            rapidapi_webhook_events_total.labels(
                event_type=event_type, status="duplicate"
            ).inc()
            return JSONResponse(
                content={
                    "status": "ok",
                    "event_type": event_type,
                    "duplicate": True,
                    "message": "Event already processed",
                },
                status_code=200,
            )

        state.idempotency.mark_in_progress(webhook_idempotency_key, ttl_s=60)

    try:
        await _process_webhook_event(event_type, event_data)

        # Store idempotency result for successful processing
        if state.idempotency and webhook_idempotency_key:
            state.idempotency.store_result(
                webhook_idempotency_key,
                {"status": "processed", "event_type": event_type, "event_id": event_id},
                ttl_s=86400,
            )
            state.idempotency.clear_in_progress(webhook_idempotency_key)

        return JSONResponse(
            content={"status": "ok", "event_type": event_type},
            status_code=200,
        )

    except Exception as e:
        logger.error(f"Error processing webhook {event_type}: {e}")
        rapidapi_webhook_events_total.labels(
            event_type=event_type, status="error"
        ).inc()

        if state.idempotency and webhook_idempotency_key:
            state.idempotency.clear_in_progress(webhook_idempotency_key)

        raise HTTPException(
            status_code=500,
            detail=f"Webhook processing error: {str(e)}",
        )


async def _process_webhook_event(event_type: str, event_data: dict) -> None:
    """Process a webhook event based on its type.

    Args:
        event_type: Type of the webhook event
        event_data: Event payload data
    """
    state = get_app_state()

    if event_type == "subscription.created":
        api_key = event_data.get("api_key")
        tier = event_data.get("tier", "free")
        user_id = event_data.get("user_id")

        if api_key:
            tier_enum = (
                SubscriptionTier(tier)
                if tier in [t.value for t in SubscriptionTier]
                else SubscriptionTier.FREE
            )
            await state.rapidapi_client._cache_tier(api_key, tier_enum, user_id)
            rapidapi_tier_cache_total.labels(operation="set").inc()
            rapidapi_tier_distribution.labels(tier=tier).inc()

            if state.rapidapi_tenant_manager and user_id:
                state.rapidapi_tenant_manager.create_tenant(
                    user_id,
                    tier_enum,
                    metadata={
                        "api_key_hash": state.rapidapi_client._hash_api_key(api_key)
                    },
                )

            logger.info(f"Cached new subscription: tier={tier}, user_id={user_id}")

        rapidapi_webhook_events_total.labels(
            event_type="subscription.created", status="success"
        ).inc()

    elif event_type == "subscription.updated":
        api_key = event_data.get("api_key")
        new_tier = event_data.get("tier", "free")
        user_id = event_data.get("user_id")

        if api_key:
            await state.rapidapi_client.invalidate_tier_cache(api_key)
            rapidapi_tier_cache_total.labels(operation="invalidate").inc()

            tier_enum = (
                SubscriptionTier(new_tier)
                if new_tier in [t.value for t in SubscriptionTier]
                else SubscriptionTier.FREE
            )
            await state.rapidapi_client._cache_tier(api_key, tier_enum, user_id)
            rapidapi_tier_cache_total.labels(operation="set").inc()
            rapidapi_tier_distribution.labels(tier=new_tier).inc()

            if state.rapidapi_tenant_manager and user_id:
                state.rapidapi_tenant_manager.update_tenant_tier(
                    user_id,
                    tier_enum,
                    metadata={
                        "api_key_hash": state.rapidapi_client._hash_api_key(api_key)
                    },
                )

            logger.info(
                f"Updated subscription: new_tier={new_tier}, user_id={user_id}"
            )

        rapidapi_webhook_events_total.labels(
            event_type="subscription.updated", status="success"
        ).inc()

    elif event_type == "subscription.cancelled":
        api_key = event_data.get("api_key")
        user_id = event_data.get("user_id")

        if api_key:
            await state.rapidapi_client.invalidate_tier_cache(api_key)
            rapidapi_tier_cache_total.labels(operation="invalidate").inc()

            if state.rapidapi_tenant_manager and user_id:
                state.rapidapi_tenant_manager.delete_tenant(user_id)

            logger.info(f"Subscription cancelled: user_id={user_id}")

        rapidapi_webhook_events_total.labels(
            event_type="subscription.cancelled", status="success"
        ).inc()

    elif event_type == "usage.alert":
        api_key = event_data.get("api_key")
        usage_percent = event_data.get("usage_percent", 0)
        threshold = event_data.get("threshold", "unknown")

        logger.warning(
            f"Usage alert: api_key_hash="
            f"{state.rapidapi_client._hash_api_key(api_key) if api_key else 'unknown'}, "
            f"usage={usage_percent}%, threshold={threshold}"
        )
        rapidapi_webhook_events_total.labels(
            event_type="usage.alert", status="success"
        ).inc()

    else:
        logger.info(f"Unknown webhook event type: {event_type}")
        rapidapi_webhook_events_total.labels(
            event_type=event_type, status="unknown_type"
        ).inc()


@router.get(
    "/rapidapi/status",
    summary="RapidAPI Integration Status",
    description="Check the status of RapidAPI integration.",
)
async def rapidapi_status(request: Request) -> JSONResponse:
    """Get RapidAPI integration status."""
    state = get_app_state()

    # Verify API key
    api_key, tenant, tier = verify_api_key(request)

    if not state.rapidapi_client:
        return JSONResponse(
            content={
                "status": "not_configured",
                "message": "RapidAPI integration not configured",
            },
            status_code=200,
        )

    # Get usage stats for the current API key
    usage_stats = (
        await state.rapidapi_client.get_usage_stats(api_key) if api_key else {}
    )

    return JSONResponse(
        content={
            "status": "configured",
            "tier": tier,
            "usage": usage_stats,
            "redis_connected": state.rapidapi_client.redis_enabled,
            "api_configured": bool(state.rapidapi_client.api_key),
        },
        status_code=200,
    )
