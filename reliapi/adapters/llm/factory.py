"""Factory for LLM adapters."""
from typing import Optional

from reliapi.adapters.llm.anthropic import AnthropicAdapter
from reliapi.adapters.llm.base import LLMAdapter
from reliapi.adapters.llm.mistral import MistralAdapter
from reliapi.adapters.llm.openai import OpenAIAdapter


def get_adapter(provider: str) -> Optional[LLMAdapter]:
    """Get LLM adapter for provider."""
    adapters = {
        "openai": OpenAIAdapter(),
        "anthropic": AnthropicAdapter(),
        "mistral": MistralAdapter(),
    }
    return adapters.get(provider.lower())


def detect_provider(base_url: str) -> Optional[str]:
    """Detect provider from base URL."""
    base_url_lower = base_url.lower()
    if "openai.com" in base_url_lower:
        return "openai"
    elif "anthropic.com" in base_url_lower:
        return "anthropic"
    elif "mistral.ai" in base_url_lower:
        return "mistral"
    return None

