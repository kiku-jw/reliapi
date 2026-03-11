"""OpenAI LLM adapter."""
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from reliapi.adapters.llm.base import LLMAdapter


class OpenAIAdapter(LLMAdapter):
    """OpenAI API adapter."""
    
    # Pricing per 1M tokens (as of 2024)
    PRICING = {
        "gpt-4": {"prompt": 30.0, "completion": 60.0},
        "gpt-4-turbo": {"prompt": 10.0, "completion": 30.0},
        "gpt-4o": {"prompt": 5.0, "completion": 15.0},
        "gpt-4o-mini": {"prompt": 0.15, "completion": 0.6},
        "gpt-3.5-turbo": {"prompt": 0.5, "completion": 1.5},
    }
    
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
        """Prepare OpenAI request payload."""
        payload = {
            "model": model,
            "messages": messages,
        }
        
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if stop is not None:
            payload["stop"] = stop
        if stream:
            payload["stream"] = True
        
        return payload
    
    def supports_streaming(self) -> bool:
        """OpenAI supports streaming."""
        return True
    
    async def stream_chat(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_path: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream chat completion from OpenAI."""
        url = f"{base_url.rstrip('/')}{api_path}"
        
        async with client.stream(
            "POST",
            url,
            json=payload,
            headers=headers,
            timeout=60.0,
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                error_data = json.loads(error_body.decode()) if error_body else {}
                raise httpx.HTTPStatusError(
                    f"OpenAI API error: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                
                # OpenAI SSE format: "data: {...}"
                if line.startswith("data: "):
                    data_str = line[6:]  # Remove "data: " prefix
                    if data_str == "[DONE]":
                        # After [DONE], OpenAI may send a final chunk with usage
                        # Continue reading to get usage if available
                        continue
                    
                    try:
                        chunk_data = json.loads(data_str)
                        # Check if this is a usage-only chunk (no choices)
                        # OpenAI sometimes sends usage in a separate chunk after content chunks
                        if "usage" in chunk_data and "choices" not in chunk_data:
                            # This is a usage-only chunk, yield it with a special marker
                            yield {"_usage_only": True, **chunk_data}
                        elif "usage" in chunk_data and "choices" in chunk_data:
                            # Some providers include usage in the same chunk as choices
                            # Extract and yield usage separately
                            usage = chunk_data.get("usage", {})
                            if usage:
                                yield {"_usage_only": True, "usage": usage}
                            # Also yield the regular chunk
                            yield chunk_data
                        else:
                            yield chunk_data
                    except json.JSONDecodeError:
                        continue
    
    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse OpenAI response to normalized format."""
        choices = response.get("choices", [])
        if not choices:
            return {
                "content": "",
                "finish_reason": "error",
            }
        
        choice = choices[0]
        message = choice.get("message", {})
        
        return {
            "content": message.get("content", ""),
            "role": message.get("role", "assistant"),
            "finish_reason": choice.get("finish_reason", "stop"),
        }
    
    def get_cost_usd(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Optional[float]:
        """Calculate cost in USD."""
        pricing = self.PRICING.get(model)
        if not pricing:
            return None
        
        prompt_cost = (prompt_tokens / 1_000_000) * pricing["prompt"]
        completion_cost = (completion_tokens / 1_000_000) * pricing["completion"]
        return prompt_cost + completion_cost

