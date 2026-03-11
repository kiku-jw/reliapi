"""Mistral AI LLM adapter."""
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from reliapi.adapters.llm.base import LLMAdapter


class MistralAdapter(LLMAdapter):
    """Mistral AI API adapter."""
    
    # Pricing per 1M tokens (as of 2024)
    PRICING = {
        "mistral-large-latest": {"prompt": 2.7, "completion": 8.1},
        "mistral-medium-latest": {"prompt": 2.7, "completion": 8.1},
        "mistral-small-latest": {"prompt": 0.2, "completion": 0.6},
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
        """Prepare Mistral request payload."""
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
        """Mistral supports streaming."""
        return True
    
    async def stream_chat(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_path: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream chat completion from Mistral.
        
        Mistral uses SSE format similar to OpenAI:
        - "data: {...}" format
        - [DONE] marker at the end
        """
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
                    f"Mistral API error: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                
                # Mistral SSE format: "data: {...}"
                if line.startswith("data: "):
                    data_str = line[6:]  # Remove "data: " prefix
                    if data_str == "[DONE]":
                        # After [DONE], Mistral may send a final chunk with usage
                        continue
                    
                    try:
                        chunk_data = json.loads(data_str)
                        
                        # Check if this is a usage-only chunk
                        if "usage" in chunk_data and "choices" not in chunk_data:
                            yield {"_usage_only": True, **chunk_data}
                        elif "usage" in chunk_data and "choices" in chunk_data:
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
        """Parse Mistral response to normalized format."""
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

