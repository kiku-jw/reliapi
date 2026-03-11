"""Anthropic Claude LLM adapter."""
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from reliapi.adapters.llm.base import LLMAdapter


class AnthropicAdapter(LLMAdapter):
    """Anthropic Claude API adapter."""
    
    # Pricing per 1M tokens (as of 2024)
    PRICING = {
        "claude-3-opus-20240229": {"prompt": 15.0, "completion": 75.0},
        "claude-3-sonnet-20240229": {"prompt": 3.0, "completion": 15.0},
        "claude-3-haiku-20240307": {"prompt": 0.25, "completion": 1.25},
        "claude-3-5-sonnet-20241022": {"prompt": 3.0, "completion": 15.0},
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
        """Prepare Anthropic request payload."""
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens or 1024,
        }
        
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if stop is not None:
            payload["stop_sequences"] = stop
        if stream:
            payload["stream"] = True
        
        return payload
    
    def supports_streaming(self) -> bool:
        """Anthropic supports streaming."""
        return True
    
    async def stream_chat(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_path: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream chat completion from Anthropic.
        
        Anthropic uses SSE format with event types:
        - message_start: Initial message metadata
        - content_block_start: Start of content block
        - content_block_delta: Delta text chunks
        - content_block_stop: End of content block
        - message_delta: Message-level deltas (usage, etc.)
        - message_stop: End of message
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
                    f"Anthropic API error: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            
            current_event_type = None
            
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                
                # Anthropic SSE format: "event: <type>" and "data: {...}"
                if line.startswith("event: "):
                    current_event_type = line[7:].strip()
                elif line.startswith("data: "):
                    data_str = line[6:]  # Remove "data: " prefix
                    try:
                        chunk_data = json.loads(data_str)
                        
                        # Yield content_block_delta events (text chunks)
                        if current_event_type == "content_block_delta":
                            delta = chunk_data.get("delta", {})
                            text = delta.get("text", "")
                            if text:
                                # Yield in OpenAI-like format for compatibility
                                yield {
                                    "choices": [{
                                        "delta": {"content": text},
                                        "finish_reason": None,
                                    }]
                                }
                        
                        # Yield message_delta for usage information
                        elif current_event_type == "message_delta":
                            usage = chunk_data.get("usage", {})
                            if usage:
                                # Yield usage in OpenAI-like format
                                yield {
                                    "_usage_only": True,
                                    "usage": {
                                        "prompt_tokens": usage.get("input_tokens", 0),
                                        "completion_tokens": usage.get("output_tokens", 0),
                                    }
                                }
                        
                        # Yield message_stop for finish reason
                        elif current_event_type == "message_stop":
                            stop_reason = chunk_data.get("stop_reason", "stop")
                            # Yield final chunk with finish reason
                            yield {
                                "choices": [{
                                    "delta": {},
                                    "finish_reason": stop_reason,
                                }]
                            }
                    except json.JSONDecodeError:
                        continue
    
    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Parse Anthropic response to normalized format."""
        content = response.get("content", [])
        if not content:
            return {
                "content": "",
                "finish_reason": "error",
            }
        
        # Anthropic returns content as list of blocks
        text_content = ""
        for block in content:
            if block.get("type") == "text":
                text_content += block.get("text", "")
        
        return {
            "content": text_content,
            "role": "assistant",
            "finish_reason": response.get("stop_reason", "stop"),
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

