"""
ReliAPI integration for LangChain.

This package provides seamless integration between ReliAPI and LangChain,
allowing you to use ReliAPI's reliability features (caching, retries, idempotency)
with LangChain applications.

Example:
    from reliapi.integrations.langchain import ReliAPIChatOpenAI
    
    llm = ReliAPIChatOpenAI(
        base_url="https://reliapi.kikuai.dev/proxy/llm",
        model="gpt-4o-mini",
        rapidapi_key="your-key"
    )
    
    response = llm.invoke("What is Python?")
"""

from .chat_models import ReliAPIChatOpenAI

__all__ = ["ReliAPIChatOpenAI"]














