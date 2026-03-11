"""Cost estimation for LLM requests."""
from typing import Dict, Optional


class CostEstimator:
    """Simple cost estimator for LLM requests.
    
    Note: Pricing data last updated: 2025-01-15
    These prices are approximate and should be updated periodically.
    For accurate pricing, refer to provider documentation:
    - OpenAI: https://openai.com/pricing
    - Anthropic: https://www.anthropic.com/pricing
    - Mistral: https://mistral.ai/pricing
    """
    
    # Approximate pricing per 1K tokens (simplified, per-model)
    # Last updated: 2025-01-15
    # These are approximate and should be updated periodically
    PRICING_PER_1K = {
        "openai": {
            "gpt-4": {"prompt": 0.03, "completion": 0.06},
            "gpt-4-turbo": {"prompt": 0.01, "completion": 0.03},
            "gpt-4o": {"prompt": 0.005, "completion": 0.015},
            "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
            "gpt-3.5-turbo": {"prompt": 0.0005, "completion": 0.0015},
        },
        "anthropic": {
            "claude-3-opus-20240229": {"prompt": 0.015, "completion": 0.075},
            "claude-3-sonnet-20240229": {"prompt": 0.003, "completion": 0.015},
            "claude-3-haiku-20240307": {"prompt": 0.00025, "completion": 0.00125},
            "claude-3-5-sonnet-20241022": {"prompt": 0.003, "completion": 0.015},
        },
        "mistral": {
            "mistral-large-latest": {"prompt": 0.0027, "completion": 0.0081},
            "mistral-medium-latest": {"prompt": 0.0027, "completion": 0.0081},
            "mistral-small-latest": {"prompt": 0.0002, "completion": 0.0006},
        },
    }
    
    @classmethod
    def estimate_cost(
        cls,
        provider: str,
        model: str,
        prompt_tokens: int,
        max_tokens: Optional[int] = None,
    ) -> Optional[float]:
        """
        Estimate cost for LLM request.
        
        Args:
            provider: Provider name (openai, anthropic, mistral)
            model: Model name
            prompt_tokens: Estimated prompt tokens (or actual if available)
            max_tokens: Maximum completion tokens (for worst-case estimate)
            
        Returns:
            Estimated cost in USD, or None if pricing unknown
        """
        pricing = cls.PRICING_PER_1K.get(provider, {}).get(model)
        if not pricing:
            return None
        
        # Estimate prompt cost
        prompt_cost = (prompt_tokens / 1000.0) * pricing["prompt"]
        
        # Estimate completion cost (worst case: max_tokens)
        completion_cost = 0.0
        if max_tokens:
            # Assume worst case: full max_tokens used
            completion_cost = (max_tokens / 1000.0) * pricing["completion"]
        else:
            # Conservative estimate: assume 50% of prompt tokens
            completion_cost = (prompt_tokens / 1000.0) * 0.5 * pricing["completion"]
        
        return prompt_cost + completion_cost
    
    @classmethod
    def estimate_from_messages(
        cls,
        provider: str,
        model: str,
        messages: list,
        max_tokens: Optional[int] = None,
    ) -> Optional[float]:
        """
        Estimate cost from messages list.
        
        Rough estimation: ~4 chars per token for English text.
        """
        # Rough token estimation: ~4 chars per token
        total_chars = sum(len(msg.get("content", "")) for msg in messages)
        estimated_prompt_tokens = total_chars // 4
        
        return cls.estimate_cost(provider, model, estimated_prompt_tokens, max_tokens)

