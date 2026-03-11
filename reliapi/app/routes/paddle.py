"""Paddle payment processing routes.

This module handles Paddle integration for subscription management and payment processing.
All operations are automated through Paddle API - no manual intervention required.
"""

import os
import hmac
import hashlib
import json
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, Request, HTTPException, Header, Body
from pydantic import BaseModel, Field

router = APIRouter(prefix="/paddle", tags=["paddle"])

# Paddle API configuration
PADDLE_API_KEY = os.getenv("PADDLE_API_KEY")
PADDLE_VENDOR_ID = os.getenv("PADDLE_VENDOR_ID")
PADDLE_ENVIRONMENT = os.getenv("PADDLE_ENVIRONMENT", "sandbox")  # sandbox or production
PADDLE_WEBHOOK_SECRET = os.getenv("PADDLE_WEBHOOK_SECRET")

# Paddle API endpoints
PADDLE_API_BASE = (
    "https://sandbox-api.paddle.com" if PADDLE_ENVIRONMENT == "sandbox" else "https://api.paddle.com"
)


class SubscriptionPlan(BaseModel):
    """Subscription plan model."""

    id: str
    name: str
    price: float
    currency: str = "USD"
    interval: str = "month"  # month, year
    requests_per_month: int
    features: list[str] = Field(default_factory=list)


class CreateCheckoutRequest(BaseModel):
    """Request to create a Paddle checkout."""

    plan_id: str
    customer_email: str
    success_url: str
    cancel_url: Optional[str] = None


class SubscriptionStatus(BaseModel):
    """Subscription status response."""

    subscription_id: str
    status: str  # active, cancelled, past_due, etc.
    plan_id: str
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False


@router.post("/checkout")
async def create_checkout(request: CreateCheckoutRequest) -> Dict[str, Any]:
    """Create a Paddle checkout session for subscription.

    This endpoint creates a checkout session through Paddle API.
    Returns checkout URL that user can redirect to.
    """
    if not PADDLE_API_KEY or not PADDLE_VENDOR_ID:
        raise HTTPException(status_code=500, detail="Paddle configuration missing")

    import httpx

    # Paddle checkout creation
    checkout_data = {
        "vendor_id": int(PADDLE_VENDOR_ID),
        "vendor_auth_code": PADDLE_API_KEY,
        "products": [int(request.plan_id)],
        "customer_email": request.customer_email,
        "success_url": request.success_url,
        "passthrough": json.dumps({"user_email": request.customer_email}),
    }

    if request.cancel_url:
        checkout_data["cancel_url"] = request.cancel_url

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{PADDLE_API_BASE}/transaction",
            json=checkout_data,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Paddle API error: {response.text}",
            )

        result = response.json()
        return {
            "checkout_id": result.get("checkout_id"),
            "checkout_url": result.get("checkout_url"),
        }


@router.get("/subscription/{subscription_id}")
async def get_subscription(subscription_id: str) -> SubscriptionStatus:
    """Get subscription status from Paddle.

    Fetches subscription details from Paddle API.
    """
    if not PADDLE_API_KEY or not PADDLE_VENDOR_ID:
        raise HTTPException(status_code=500, detail="Paddle configuration missing")

    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{PADDLE_API_BASE}/subscription/{subscription_id}",
            headers={
                "Authorization": f"Bearer {PADDLE_API_KEY}",
            },
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=response.status_code,
                detail=f"Paddle API error: {response.text}",
            )

        data = response.json()
        return SubscriptionStatus(
            subscription_id=data.get("subscription_id"),
            status=data.get("status"),
            plan_id=data.get("plan_id"),
            current_period_end=datetime.fromisoformat(data["current_period_end"].replace("Z", "+00:00"))
            if data.get("current_period_end")
            else None,
            cancel_at_period_end=data.get("cancel_at_period_end", False),
        )


@router.post("/webhook")
async def paddle_webhook(
    request: Request,
    paddle_signature: Optional[str] = Header(None, alias="Paddle-Signature"),
) -> Dict[str, str]:
    """Handle Paddle webhook events.

    Processes webhook events from Paddle:
    - subscription.created
    - subscription.updated
    - subscription.cancelled
    - transaction.completed
    - transaction.payment_failed

    All events are processed automatically without manual intervention.
    """
    if not PADDLE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Paddle webhook secret not configured")

    body = await request.body()

    # Verify webhook signature
    if paddle_signature:
        expected_signature = hmac.new(
            PADDLE_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(paddle_signature, expected_signature):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event = await request.json()
    event_type = event.get("event_type")
    event_data = event.get("data", {})

    # Import subscription service
    from reliapi.app.business_services.subscription import SubscriptionService

    # Process different event types automatically
    if event_type == "subscription.created":
        # New subscription created - activate user account
        subscription_id = event_data.get("subscription_id")
        customer_email = event_data.get("customer_email")
        plan_id = event_data.get("plan_id")

        # Activate subscription automatically
        SubscriptionService.activate_subscription(
            user_id=customer_email,
            subscription_id=subscription_id,
            plan_id=plan_id,
            paddle_subscription_id=subscription_id,
        )

    elif event_type == "subscription.updated":
        # Subscription updated (plan change, etc.)
        subscription_id = event_data.get("subscription_id")
        status = event_data.get("status")
        customer_email = event_data.get("customer_email")
        plan_id = event_data.get("plan_id")

        # Update subscription automatically
        if status == "active":
            SubscriptionService.activate_subscription(
                user_id=customer_email,
                subscription_id=subscription_id,
                plan_id=plan_id,
                paddle_subscription_id=subscription_id,
            )
        elif status == "cancelled":
            SubscriptionService.cancel_subscription(
                user_id=customer_email, cancel_at_period_end=False
            )

    elif event_type == "subscription.cancelled":
        # Subscription cancelled
        subscription_id = event_data.get("subscription_id")
        customer_email = event_data.get("customer_email")

        # Cancel subscription automatically
        SubscriptionService.cancel_subscription(
            user_id=customer_email, cancel_at_period_end=True
        )

    elif event_type == "transaction.completed":
        # Payment completed
        transaction_id = event_data.get("transaction_id")
        subscription_id = event_data.get("subscription_id")

        # TODO: Record payment in database
        print(f"Transaction completed: {transaction_id} for subscription {subscription_id}")

    elif event_type == "transaction.payment_failed":
        # Payment failed
        transaction_id = event_data.get("transaction_id")
        subscription_id = event_data.get("subscription_id")

        # TODO: Handle failed payment (notify user, etc.)
        print(f"Payment failed: {transaction_id} for subscription {subscription_id}")

    return {"status": "processed"}


@router.get("/plans")
async def list_plans() -> list[SubscriptionPlan]:
    """List available subscription plans.

    Returns list of subscription plans configured in Paddle.
    """
    # Predefined plans based on MARKETING_STRATEGY.md
    plans = [
        SubscriptionPlan(
            id="free",
            name="Free",
            price=0.0,
            interval="month",
            requests_per_month=10000,
            features=["All core features", "Community support"],
        ),
        SubscriptionPlan(
            id="pro",
            name="Pro",
            price=49.0,
            interval="month",
            requests_per_month=100000,
            features=[
                "100K requests/month",
                "Email support (24h)",
                "Advanced analytics",
            ],
        ),
        SubscriptionPlan(
            id="team",
            name="Team",
            price=199.0,
            interval="month",
            requests_per_month=500000,
            features=[
                "500K requests/month",
                "Up to 10 team members",
                "Priority support (4h)",
                "SSO, audit logs",
            ],
        ),
    ]

    return plans

