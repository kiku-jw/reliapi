"""
ReliAPI integration for LlamaIndex.

This package provides seamless integration between ReliAPI and LlamaIndex,
allowing you to use ReliAPI's reliability features (caching, retries, idempotency)
with LlamaIndex applications.

Example:
    from reliapi.integrations.llamaindex import ReliAPIOpenAI
    
    llm = ReliAPIOpenAI(
        api_base="https://reliapi.kikuai.dev/proxy/llm",
        model="gpt-4o-mini",
        rapidapi_key="your-key"
    )
    
    response = llm.complete("What is Python?")
"""

from .llm import ReliAPIOpenAI

__all__ = ["ReliAPIOpenAI"]














