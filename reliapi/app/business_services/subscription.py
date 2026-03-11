"""Subscription management service.

This module handles subscription management logic, including:
- Subscription activation/deactivation
- Usage tracking and limits
- Plan upgrades/downgrades

All operations are automated - no manual intervention required.
"""

import os
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from enum import Enum
import redis
import json

redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


class SubscriptionTier(str, Enum):
    """Subscription tier enumeration."""

    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    ENTERPRISE = "enterprise"


class SubscriptionService:
    """Service for managing user subscriptions."""

    # Plan limits based on MARKETING_STRATEGY.md
    PLAN_LIMITS = {
        SubscriptionTier.FREE: {
            "requests_per_month": 10000,
            "features": ["All core features", "Community support"],
        },
        SubscriptionTier.PRO: {
            "requests_per_month": 100000,
            "features": [
                "100K requests/month",
                "Email support (24h)",
                "Advanced analytics",
            ],
        },
        SubscriptionTier.TEAM: {
            "requests_per_month": 500000,
            "features": [
                "500K requests/month",
                "Up to 10 team members",
                "Priority support (4h)",
                "SSO, audit logs",
            ],
        },
    }

    @staticmethod
    def get_user_subscription(user_id: str) -> Optional[Dict[str, Any]]:
        """Get user subscription details.

        Args:
            user_id: User identifier (email or API key)

        Returns:
            Subscription details or None if not found
        """
        key = f"subscription:{user_id}"
        data = redis_client.get(key)
        if data:
            return json.loads(data)
        return None

    @staticmethod
    def activate_subscription(
        user_id: str,
        subscription_id: str,
        plan_id: str,
        paddle_subscription_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Activate user subscription.

        Args:
            user_id: User identifier
            subscription_id: Internal subscription ID
            plan_id: Plan ID (free, pro, team)
            paddle_subscription_id: Paddle subscription ID (if paid)

        Returns:
            Subscription details
        """
        tier = SubscriptionTier(plan_id.lower())
        limits = SubscriptionService.PLAN_LIMITS.get(tier, {})

        subscription = {
            "user_id": user_id,
            "subscription_id": subscription_id,
            "paddle_subscription_id": paddle_subscription_id,
            "tier": tier.value,
            "plan_id": plan_id,
            "status": "active",
            "activated_at": datetime.utcnow().isoformat(),
            "requests_per_month": limits.get("requests_per_month", 0),
            "features": limits.get("features", []),
            "current_period_start": datetime.utcnow().isoformat(),
            "current_period_end": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        }

        # Store in Redis
        key = f"subscription:{user_id}"
        redis_client.setex(key, 86400 * 35, json.dumps(subscription))  # 35 days TTL

        # Also store by subscription_id for lookup
        sub_key = f"subscription_by_id:{subscription_id}"
        redis_client.setex(sub_key, 86400 * 35, json.dumps(subscription))

        return subscription

    @staticmethod
    def cancel_subscription(user_id: str, cancel_at_period_end: bool = True) -> bool:
        """Cancel user subscription.

        Args:
            user_id: User identifier
            cancel_at_period_end: If True, cancel at end of billing period

        Returns:
            True if cancelled successfully
        """
        subscription = SubscriptionService.get_user_subscription(user_id)
        if not subscription:
            return False

        if cancel_at_period_end:
            subscription["cancel_at_period_end"] = True
            subscription["status"] = "cancelling"
        else:
            subscription["status"] = "cancelled"
            subscription["cancelled_at"] = datetime.utcnow().isoformat()
            # Downgrade to free tier
            SubscriptionService.activate_subscription(
                user_id, subscription["subscription_id"], "free"
            )

        # Update in Redis
        key = f"subscription:{user_id}"
        redis_client.setex(key, 86400 * 35, json.dumps(subscription))

        return True

    @staticmethod
    def check_usage_limit(user_id: str, requests_this_month: int) -> tuple[bool, Optional[str]]:
        """Check if user has exceeded usage limits.

        Args:
            user_id: User identifier
            requests_this_month: Number of requests made this month

        Returns:
            Tuple of (within_limit, error_message)
        """
        subscription = SubscriptionService.get_user_subscription(user_id)
        if not subscription:
            # Default to free tier
            limit = SubscriptionService.PLAN_LIMITS[SubscriptionTier.FREE]["requests_per_month"]
        else:
            limit = subscription.get("requests_per_month", 10000)

        if requests_this_month >= limit:
            return False, f"Monthly request limit ({limit}) exceeded. Please upgrade your plan."

        # Check if at 80% threshold
        if requests_this_month >= limit * 0.8:
            return True, f"Warning: You've used {requests_this_month}/{limit} requests this month (80%)"

        return True, None

    @staticmethod
    def track_usage(user_id: str, request_count: int = 1) -> int:
        """Track API usage for a user.

        Args:
            user_id: User identifier
            request_count: Number of requests to add

        Returns:
            Total requests this month
        """
        now = datetime.utcnow()
        month_key = f"{now.year}-{now.month:02d}"
        usage_key = f"usage:{user_id}:{month_key}"

        # Increment usage counter
        total = redis_client.incrby(usage_key, request_count)
        # Set expiry to end of month + 1 day
        days_in_month = (datetime(now.year, now.month % 12 + 1, 1) - timedelta(days=1)).day
        redis_client.expire(usage_key, (days_in_month - now.day + 1) * 86400)

        return total

