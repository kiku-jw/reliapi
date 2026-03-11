"""Base LLM adapter interface."""
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict, List, Optional


class LLMAdapter(ABC):
    """Base class for LLM provider adapters."""
    
    @abstractmethod
    def prepare_request(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        stop: Optional[List[str]] = None,
        stream: bool = False,
        **kwargs,
    ) -> Dict[str, Any]:
        """Prepare provider-specific request payload."""
        pass
    
    @abstractmethod
    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse provider response to normalized format.
        
        Must return:
        {
            "content": str,           # Text content (required)
            "role": str,              # "assistant" (required, default "assistant")
            "finish_reason": str,     # "stop", "length", "error", etc. (required)
        }
        
        All adapters must return the same structure for consistency.
        """
        pass
    
    @abstractmethod
    def get_cost_usd(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Optional[float]:
        """Calculate cost in USD (if available)."""
        pass
    
    def supports_streaming(self) -> bool:
        """Check if adapter supports streaming.
        
        Override in subclasses that support streaming.
        """
        return False
    
    async def stream_chat(
        self,
        client: Any,  # httpx.AsyncClient
        base_url: str,
        api_path: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream chat completion from provider.
        
        Must yield provider-specific chunk dictionaries.
        Override in subclasses that support streaming.
        
        Raises:
            NotImplementedError: If streaming is not supported
        """
        raise NotImplementedError("Streaming not supported for this provider")

