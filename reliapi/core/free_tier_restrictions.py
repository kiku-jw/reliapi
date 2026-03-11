"""Free tier restrictions and validations."""
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Allowed models for Free tier
FREE_TIER_ALLOWED_MODELS = {
    "openai": ["gpt-4o-mini", "gpt-3.5-turbo"],
    "anthropic": ["claude-3-haiku-20240307", "claude-3-5-haiku-20241022"],
    "mistral": ["mistral-small", "mistral-tiny"],
}

# Blocked models for Free tier
FREE_TIER_BLOCKED_MODELS = {
    "openai": ["gpt-4", "gpt-4-turbo", "gpt-4o", "gpt-4-turbo-preview"],
    "anthropic": ["claude-3-opus", "claude-3-sonnet", "claude-3-5-sonnet", "claude-3-5-opus"],
    "mistral": ["mistral-large", "mistral-medium"],
}


class FreeTierRestrictions:
    """Validations and restrictions for Free tier accounts."""

    @staticmethod
    def is_model_allowed(provider: str, model: str, tier: str) -> tuple[bool, Optional[str]]:
        """
        Check if model is allowed for the tier.

        Args:
            provider: LLM provider (openai, anthropic, mistral)
            model: Model name
            tier: Account tier (free, developer, pro)

        Returns:
            Tuple of (allowed, error_message)
        """
        if tier != "free":
            # Developer and Pro tiers can use any model
            return True, None

        model_name = model.lower()
        # Free tier: check if model is in allowed list
        allowed = [
            allowed_model.lower() for allowed_model in FREE_TIER_ALLOWED_MODELS.get(provider, [])
        ]
        blocked = [
            blocked_model.lower() for blocked_model in FREE_TIER_BLOCKED_MODELS.get(provider, [])
        ]

        # Check if explicitly blocked
        if model_name in blocked:
            return False, "FREE_TIER_MODEL_NOT_ALLOWED"

        # Check if in allowed list (if list is not empty)
        if allowed and model_name not in allowed:
            return False, "FREE_TIER_MODEL_NOT_ALLOWED"

        return True, None

    @staticmethod
    def is_feature_allowed(feature: str, tier: str) -> tuple[bool, Optional[str]]:
        """
        Check if feature is allowed for the tier.

        Args:
            feature: Feature name (idempotency, soft_caps, long_fallbacks, semantic_caching, streaming, deep_idempotency)
            tier: Account tier

        Returns:
            Tuple of (allowed, error_message)
        """
        if tier != "free":
            # Developer and Pro tiers have access to all features
            return True, None

        # Free tier restrictions (SECURITY: No heavy features)
        free_tier_blocked_features = {
            "idempotency": "FREE_TIER_FEATURE_NOT_AVAILABLE",
            "deep_idempotency": "FREE_TIER_FEATURE_NOT_AVAILABLE",  # Deep idempotency with coalescing
            "soft_caps": "FREE_TIER_FEATURE_NOT_AVAILABLE",
            "long_fallbacks": "FREE_TIER_FEATURE_NOT_AVAILABLE",  # Chaining fallbacks (>1 provider)
            "semantic_caching": "FREE_TIER_FEATURE_NOT_AVAILABLE",
            "advanced_retries": "FREE_TIER_FEATURE_NOT_AVAILABLE",
            "streaming": "FREE_TIER_FEATURE_NOT_AVAILABLE",  # SSE streaming
        }

        if feature in free_tier_blocked_features:
            return False, free_tier_blocked_features[feature]

        return True, None

    @staticmethod
    def get_max_retries(tier: str) -> int:
        """Get maximum retries allowed for tier."""
        if tier == "free":
            return 1  # Only 1 retry for free tier
        elif tier == "developer":
            return 3
        else:  # pro
            return 5

    @staticmethod
    def get_max_fallback_chain_length(tier: str) -> int:
        """Get maximum fallback chain length for tier."""
        if tier == "free":
            return 1  # No fallbacks for free tier
        elif tier == "developer":
            return 2
        else:  # pro
            return 5  # Unlimited for pro

    @staticmethod
    def validate_request(
        provider: str,
        model: Optional[str],
        features: Dict[str, Any],
        tier: str,
    ) -> tuple[bool, Optional[str]]:
        """
        Validate request against tier restrictions.

        Args:
            provider: LLM provider
            model: Model name (for LLM requests)
            features: Request features (idempotency_key, soft_cap, fallback_targets, etc.)
            tier: Account tier

        Returns:
            Tuple of (allowed, error_message)
        """
        # Check model (for LLM requests)
        if model:
            allowed, error = FreeTierRestrictions.is_model_allowed(provider, model, tier)
            if not allowed:
                return False, error

        # Check idempotency
        if features.get("idempotency_key"):
            allowed, error = FreeTierRestrictions.is_feature_allowed("idempotency", tier)
            if not allowed:
                return False, error

        # Check soft caps
        if features.get("soft_cost_cap_usd"):
            allowed, error = FreeTierRestrictions.is_feature_allowed("soft_caps", tier)
            if not allowed:
                return False, error

        # Check fallback chain length
        fallback_targets = features.get("fallback_targets", [])
        if len(fallback_targets) > FreeTierRestrictions.get_max_fallback_chain_length(tier):
            return False, "FREE_TIER_FEATURE_NOT_AVAILABLE"

        return True, None
