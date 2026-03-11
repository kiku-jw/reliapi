"""
LangChain ChatOpenAI wrapper for ReliAPI.

This module provides a drop-in replacement for LangChain's ChatOpenAI
that routes requests through ReliAPI for automatic retries, caching, and idempotency.
"""

from typing import Optional, Dict, Any
from langchain_openai import ChatOpenAI


class ReliAPIChatOpenAI(ChatOpenAI):
    """
    LangChain ChatOpenAI wrapper that uses ReliAPI as the base URL.
    
    This class extends ChatOpenAI and automatically configures it to use ReliAPI,
    providing all of ReliAPI's reliability features:
    - Automatic retries
    - Smart caching
    - Idempotency protection
    - Budget caps
    - Circuit breaker
    - Cost tracking
    
    Example:
        from reliapi.integrations.langchain import ReliAPIChatOpenAI
        
        llm = ReliAPIChatOpenAI(
            base_url="https://reliapi.kikuai.dev/proxy/llm",
            model="gpt-4o-mini",
            rapidapi_key="your-rapidapi-key"
        )
        
        response = llm.invoke("What is Python?")
    """
    
    def __init__(
        self,
        base_url: str = "https://reliapi.kikuai.dev/proxy/llm",
        rapidapi_key: Optional[str] = None,
        reliapi_key: Optional[str] = None,
        **kwargs
    ):
        """
        Initialize ReliAPI ChatOpenAI wrapper.
        
        Args:
            base_url: ReliAPI base URL (default: RapidAPI endpoint)
            rapidapi_key: RapidAPI key (for RapidAPI usage)
            reliapi_key: ReliAPI API key (for self-hosted usage)
            **kwargs: Additional arguments passed to ChatOpenAI
        
        Note:
            Either rapidapi_key or reliapi_key must be provided.
            If using RapidAPI, set rapidapi_key.
            If using self-hosted ReliAPI, set reliapi_key.
        """
        # Set default headers based on authentication method
        default_headers: Dict[str, str] = {}
        
        if rapidapi_key:
            default_headers["X-RapidAPI-Key"] = rapidapi_key
        elif reliapi_key:
            default_headers["Authorization"] = f"Bearer {reliapi_key}"
        else:
            raise ValueError("Either rapidapi_key or reliapi_key must be provided")
        
        # Merge with any provided headers
        headers = kwargs.pop("default_headers", {})
        headers.update(default_headers)
        
        # Initialize parent class with ReliAPI base URL
        super().__init__(
            base_url=base_url,
            default_headers=headers,
            **kwargs
        )
    
    def invoke(
        self,
        input: Any,
        config: Optional[Any] = None,
        **kwargs: Any
    ) -> Any:
        """
        Invoke the LLM with ReliAPI reliability features.
        
        Args:
            input: Input to the LLM (string or messages)
            config: Optional runtime configuration
            **kwargs: Additional arguments
        
        Returns:
            LLM response with ReliAPI metadata
        
        Note:
            This method automatically benefits from:
            - Caching (if same request was made before)
            - Idempotency (if idempotency_key is provided)
            - Automatic retries (on failures)
            - Cost tracking (in response metadata)
        """
        # Add idempotency key if not present
        if config and hasattr(config, "run_id"):
            if "default_headers" not in kwargs:
                kwargs["default_headers"] = {}
            if "X-Idempotency-Key" not in kwargs["default_headers"]:
                kwargs["default_headers"]["X-Idempotency-Key"] = f"langchain-{config.run_id}"
        
        return super().invoke(input, config=config, **kwargs)














