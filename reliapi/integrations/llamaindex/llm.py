"""
LlamaIndex OpenAI wrapper for ReliAPI.

This module provides a drop-in replacement for LlamaIndex's OpenAI LLM
that routes requests through ReliAPI for automatic retries, caching, and idempotency.
"""

from typing import Optional, Dict, Any
from llama_index.llms.openai import OpenAI


class ReliAPIOpenAI(OpenAI):
    """
    LlamaIndex OpenAI wrapper that uses ReliAPI as the base URL.
    
    This class extends OpenAI and automatically configures it to use ReliAPI,
    providing all of ReliAPI's reliability features:
    - Automatic retries
    - Smart caching
    - Idempotency protection
    - Budget caps
    - Circuit breaker
    - Cost tracking
    
    Example:
        from reliapi.integrations.llamaindex import ReliAPIOpenAI
        
        llm = ReliAPIOpenAI(
            api_base="https://reliapi.kikuai.dev/proxy/llm",
            model="gpt-4o-mini",
            rapidapi_key="your-rapidapi-key"
        )
        
        response = llm.complete("What is Python?")
    """
    
    def __init__(
        self,
        api_base: str = "https://reliapi.kikuai.dev/proxy/llm",
        rapidapi_key: Optional[str] = None,
        reliapi_key: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize ReliAPI OpenAI wrapper.
        
        Args:
            api_base: ReliAPI base URL (default: RapidAPI endpoint)
            rapidapi_key: RapidAPI key (for RapidAPI usage)
            reliapi_key: ReliAPI API key (for self-hosted usage)
            **kwargs: Additional arguments passed to OpenAI
        
        Note:
            Either rapidapi_key or reliapi_key must be provided.
            If using RapidAPI, set rapidapi_key.
            If using self-hosted ReliAPI, set reliapi_key.
        """
        # Set default headers based on authentication method
        additional_kwargs = kwargs.pop("additional_kwargs", {})
        headers = additional_kwargs.get("headers", {})
        
        if rapidapi_key:
            headers["X-RapidAPI-Key"] = rapidapi_key
        elif reliapi_key:
            headers["Authorization"] = f"Bearer {reliapi_key}"
        else:
            raise ValueError("Either rapidapi_key or reliapi_key must be provided")
        
        additional_kwargs["headers"] = headers
        
        # Use dummy API key (ReliAPI handles actual auth)
        api_key = kwargs.pop("api_key", rapidapi_key or reliapi_key or "dummy-key")
        
        # Initialize parent class with ReliAPI base URL
        super().__init__(
            api_base=api_base,
            api_key=api_key,
            additional_kwargs=additional_kwargs,
            **kwargs
        )
    
    def complete(
        self,
        prompt: str,
        **kwargs: Any
    ) -> Any:
        """
        Complete a prompt with ReliAPI reliability features.
        
        Args:
            prompt: Prompt text
            **kwargs: Additional arguments
        
        Returns:
            Completion response with ReliAPI metadata
        
        Note:
            This method automatically benefits from:
            - Caching (if same prompt was used before)
            - Idempotency (if idempotency_key is provided)
            - Automatic retries (on failures)
            - Cost tracking (in response metadata)
        """
        return super().complete(prompt, **kwargs)
    
    def stream_complete(
        self,
        prompt: str,
        **kwargs: Any
    ) -> Any:
        """
        Stream completion with ReliAPI reliability features.
        
        Args:
            prompt: Prompt text
            **kwargs: Additional arguments
        
        Returns:
            Streaming completion response
        
        Note:
            Streaming requests also benefit from ReliAPI's reliability features.
        """
        return super().stream_complete(prompt, **kwargs)














