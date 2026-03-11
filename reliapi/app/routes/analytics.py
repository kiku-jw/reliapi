"""Analytics and tracking routes.

This module provides analytics endpoints for tracking user behavior,
conversion funnels, and events. All tracking is automated through APIs.
"""
import base64
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Analytics providers configuration
GOOGLE_ANALYTICS_ID = os.getenv("GOOGLE_ANALYTICS_ID")
MIXPANEL_TOKEN = os.getenv("MIXPANEL_TOKEN")
POSTHOG_API_KEY = os.getenv("POSTHOG_API_KEY")
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "https://app.posthog.com")


class Event(BaseModel):
    """Analytics event model."""

    event_name: str
    user_id: Optional[str] = None
    properties: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[datetime] = None


class ConversionEvent(BaseModel):
    """Conversion event model."""

    event_type: str  # signup, trial_start, paid_conversion, etc.
    user_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)


@router.post("/track")
async def track_event(
    event: Event,
    request: Request,
) -> Dict[str, str]:
    """Track analytics event.

    Automatically sends event to configured analytics providers:
    - Google Analytics (if configured)
    - Mixpanel (if configured)
    - PostHog (if configured)

    All tracking is automated - no manual intervention required.
    """
    # Get user IP and user agent for additional context
    user_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    # Prepare event data
    event_data = {
        "event_name": event.event_name,
        "user_id": event.user_id,
        "properties": {
            **event.properties,
            "ip": user_ip,
            "user_agent": user_agent,
            "timestamp": (event.timestamp or datetime.utcnow()).isoformat(),
        },
    }

    # Track in Google Analytics (Measurement Protocol)
    if GOOGLE_ANALYTICS_ID:
        await _track_google_analytics(event_data, GOOGLE_ANALYTICS_ID)

    # Track in Mixpanel
    if MIXPANEL_TOKEN:
        await _track_mixpanel(event_data, MIXPANEL_TOKEN)

    # Track in PostHog
    if POSTHOG_API_KEY:
        await _track_posthog(event_data, POSTHOG_API_KEY, POSTHOG_HOST)

    return {"status": "tracked", "event": event.event_name}


@router.post("/conversion")
async def track_conversion(
    conversion: ConversionEvent,
    request: Request,
) -> Dict[str, str]:
    """Track conversion event.

    Tracks conversion events in the funnel:
    - Visitor → Trial → Paid

    All tracking is automated.
    """
    event = Event(
        event_name=f"conversion_{conversion.event_type}",
        user_id=conversion.user_id,
        properties={
            **conversion.properties,
            "conversion_type": conversion.event_type,
        },
    )

    return await track_event(event, request)


@router.get("/funnel")
async def get_conversion_funnel(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Get conversion funnel metrics.

    Returns conversion funnel data:
    - Visitors
    - Trial signups
    - Paid conversions
    - Conversion rates

    All metrics are calculated automatically.
    """
    # Parse dates
    if start_date:
        start = datetime.fromisoformat(start_date)
    else:
        start = datetime.utcnow() - timedelta(days=30)

    if end_date:
        end = datetime.fromisoformat(end_date)
    else:
        end = datetime.utcnow()

    # TODO: Query analytics data from storage
    # For now, return structure
    return {
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "funnel": {
            "visitors": 0,  # Would be calculated from analytics
            "trial_signups": 0,
            "paid_conversions": 0,
        },
        "conversion_rates": {
            "visitor_to_trial": 0.0,
            "trial_to_paid": 0.0,
            "overall": 0.0,
        },
    }


async def _track_google_analytics(event_data: Dict[str, Any], ga_id: str) -> None:
    """Track event in Google Analytics via Measurement Protocol."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://www.google-analytics.com/mp/collect",
                params={
                    "measurement_id": ga_id,
                    "api_secret": os.getenv("GOOGLE_ANALYTICS_API_SECRET", ""),
                },
                json={
                    "client_id": event_data.get("user_id", "anonymous"),
                    "events": [
                        {
                            "name": event_data["event_name"],
                            "params": event_data.get("properties", {}),
                        }
                    ],
                },
                timeout=5.0,
            )
    except Exception as e:
        logger.warning(f"Google Analytics tracking error: {e}")


async def _track_mixpanel(event_data: Dict[str, Any], token: str) -> None:
    """Track event in Mixpanel."""
    try:
        # Mixpanel uses base64 encoded JSON
        event_payload = {
            "event": event_data["event_name"],
            "properties": {
                "token": token,
                "distinct_id": event_data.get("user_id", "anonymous"),
                **event_data.get("properties", {}),
            },
        }

        encoded_data = base64.b64encode(json.dumps(event_payload).encode()).decode()

        async with httpx.AsyncClient() as client:
            await client.get(
                "https://api.mixpanel.com/track",
                params={"data": encoded_data},
                timeout=5.0,
            )
    except Exception as e:
        logger.warning(f"Mixpanel tracking error: {e}")


async def _track_posthog(event_data: Dict[str, Any], api_key: str, host: str) -> None:
    """Track event in PostHog."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{host}/capture/",
                json={
                    "api_key": api_key,
                    "event": event_data["event_name"],
                    "distinct_id": event_data.get("user_id", "anonymous"),
                    "properties": event_data.get("properties", {}),
                },
                timeout=5.0,
            )
    except Exception as e:
        logger.warning(f"PostHog tracking error: {e}")
