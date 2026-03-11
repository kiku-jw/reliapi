"""Request/Response schemas for ReliAPI.

This module provides Pydantic models for:
- HTTP proxy requests and responses
- LLM proxy requests and responses
- Error and metadata structures
"""
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class HTTPMethod(str, Enum):
    """Supported HTTP methods."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class MessageRole(str, Enum):
    """LLM message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class CostPolicy(str, Enum):
    """Cost policy types."""

    NONE = "none"
    SOFT_CAP_THROTTLED = "soft_cap_throttled"
    HARD_CAP_REJECTED = "hard_cap_rejected"


class ErrorSource(str, Enum):
    """Error source types."""

    RELIAPI = "reliapi"
    UPSTREAM = "upstream"


class ChatMessage(BaseModel):
    """LLM chat message structure."""

    role: MessageRole = Field(..., description="Message role: system, user, or assistant")
    content: str = Field(..., description="Message content")


class HTTPProxyRequest(BaseModel):
    """Request schema for POST /proxy/http.

    Use this endpoint to proxy any HTTP API request with reliability layers:
    - Retries with exponential backoff
    - Circuit breaker per target
    - TTL cache for GET/HEAD requests
    - Idempotency with request coalescing
    """

    target: str = Field(
        ..., description="Target name from config.yaml (e.g., 'my_api')"
    )
    method: str = Field(
        ...,
        description="HTTP method: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS",
    )
    path: str = Field(
        ..., description="API path (e.g., '/users/123' or '/api/v1/data')"
    )
    headers: Optional[Dict[str, str]] = Field(
        None, description="HTTP headers to include in request"
    )
    query: Optional[Dict[str, Any]] = Field(
        None, description="Query parameters (e.g., {'page': 1, 'limit': 10})"
    )
    body: Optional[str] = Field(
        None, description="Request body as JSON string (for POST/PUT/PATCH)"
    )
    idempotency_key: Optional[str] = Field(
        None,
        description=(
            "Idempotency key for request coalescing. "
            "Concurrent requests with same key execute once."
        ),
    )
    cache: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Cache TTL in seconds (overrides config default). "
            "Only applies to GET/HEAD requests."
        ),
    )

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        """Validate HTTP method is uppercase and supported."""
        v = v.upper()
        valid_methods = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
        if v not in valid_methods:
            raise ValueError(f"Invalid HTTP method: {v}. Must be one of {valid_methods}")
        return v


class LLMProxyRequest(BaseModel):
    """Request schema for POST /proxy/llm.

    Make idempotent LLM API calls with predictable costs.
    Supports OpenAI, Anthropic, and Mistral.

    Features:
    - Idempotency: duplicate requests return cached result
    - Budget caps: hard cap (reject) and soft cap (throttle)
    - Caching: TTL cache for LLM responses
    - Retries: automatic retries on failures
    """

    target: str = Field(
        ...,
        description="LLM target name from config.yaml (e.g., 'openai', 'anthropic')",
    )
    messages: List[Dict[str, str]] = Field(
        ...,
        min_length=1,
        description=(
            "Messages list with 'role' and 'content' "
            "(e.g., [{'role': 'user', 'content': 'Hello'}])"
        ),
    )
    model: Optional[str] = Field(
        None,
        description=(
            "Model name (e.g., 'gpt-4o-mini', 'claude-3-haiku'). "
            "Uses default from config if not specified."
        ),
    )
    max_tokens: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Maximum tokens in response (limited by config max_tokens and budget caps)"
        ),
    )
    temperature: Optional[float] = Field(
        None,
        ge=0.0,
        le=2.0,
        description="Temperature for sampling (0.0-2.0, limited by config)",
    )
    top_p: Optional[float] = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Top-p sampling parameter (0.0-1.0)",
    )
    stop: Optional[List[str]] = Field(
        None, description="Stop sequences (e.g., ['\\n', 'END'])"
    )
    stream: bool = Field(
        False,
        description=(
            "Streaming mode. If true, returns Server-Sent Events (SSE) stream. "
            "If false or omitted, returns standard JSON response."
        ),
    )
    idempotency_key: Optional[str] = Field(
        None,
        description=(
            "Idempotency key for request coalescing. "
            "Use same key for duplicate requests to avoid duplicate LLM calls."
        ),
    )
    cache: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Cache TTL in seconds (overrides config default). "
            "Cached responses return instantly without LLM call."
        ),
    )


class TokenUsage(BaseModel):
    """Token usage statistics for LLM responses."""

    prompt_tokens: int = Field(..., ge=0, description="Number of tokens in the prompt")
    completion_tokens: int = Field(
        ..., ge=0, description="Number of tokens in the completion"
    )
    total_tokens: int = Field(..., ge=0, description="Total tokens used")
    estimated_cost_usd: Optional[float] = Field(
        None, ge=0, description="Estimated cost in USD"
    )


class LLMResponseData(BaseModel):
    """LLM response data structure."""

    content: str = Field(..., description="Generated text content")
    model: str = Field(..., description="Model used for generation")
    usage: Optional[TokenUsage] = Field(None, description="Token usage statistics")
    finish_reason: Optional[str] = Field(
        None, description="Reason for completion (stop, length, etc.)"
    )


class ErrorDetail(BaseModel):
    """Error detail in response."""

    type: str = Field(..., description="Error type")
    code: str = Field(..., description="Error code")
    message: str = Field(..., description="Error message")
    retryable: bool = Field(..., description="Whether error is retryable")
    target: Optional[str] = Field(None, description="Target name if applicable")
    status_code: Optional[int] = Field(None, description="HTTP status code")
    source: Optional[str] = Field(
        None, description="Error source: 'reliapi' or 'upstream'"
    )
    retry_after_s: Optional[float] = Field(
        None, ge=0, description="Retry after seconds (for rate limit errors)"
    )
    provider_key_status: Optional[str] = Field(
        None, description="Provider key status if applicable"
    )
    hint: Optional[str] = Field(None, description="Hint for debugging")
    details: Optional[Dict[str, Any]] = Field(
        None, description="Additional error details"
    )


class MetaResponse(BaseModel):
    """Metadata in response."""

    target: Optional[str] = Field(None, description="Target name")
    provider: Optional[str] = Field(None, description="Provider name (for LLM)")
    model: Optional[str] = Field(None, description="Model name (for LLM)")
    cache_hit: bool = Field(False, description="Whether response was from cache")
    idempotent_hit: bool = Field(
        False, description="Whether response was from idempotency cache"
    )
    retries: int = Field(0, ge=0, description="Number of retries")
    duration_ms: int = Field(..., ge=0, description="Request duration in milliseconds")
    request_id: str = Field(..., description="Request ID")
    trace_id: Optional[str] = Field(None, description="Trace ID")
    cost_usd: Optional[float] = Field(
        None, ge=0, description="Actual cost in USD (for LLM)"
    )
    cost_estimate_usd: Optional[float] = Field(
        None, ge=0, description="Estimated cost before request (for LLM)"
    )
    cost_policy_applied: Optional[str] = Field(
        None,
        description="Cost policy applied: none, soft_cap_throttled, hard_cap_rejected",
    )
    max_tokens_reduced: Optional[bool] = Field(
        None,
        description=(
            "Whether max_tokens was automatically reduced due to soft cost cap (for LLM)"
        ),
    )
    original_max_tokens: Optional[int] = Field(
        None, description="Original max_tokens before reduction (for LLM)"
    )
    fallback_used: Optional[bool] = Field(
        None, description="Whether fallback was used"
    )
    fallback_target: Optional[str] = Field(
        None, description="Fallback target name if used"
    )
    # RouteLLM correlation fields
    routellm_decision_id: Optional[str] = Field(
        None, description="RouteLLM routing decision ID for correlation"
    )
    routellm_route_name: Optional[str] = Field(
        None, description="RouteLLM route name that was applied"
    )
    routellm_provider_override: Optional[str] = Field(
        None, description="Provider override from RouteLLM (if any)"
    )
    routellm_model_override: Optional[str] = Field(
        None, description="Model override from RouteLLM (if any)"
    )


class SuccessResponse(BaseModel):
    """Success response format."""

    success: Literal[True] = Field(True, description="Success flag")
    data: Dict[str, Any] = Field(..., description="Response data")
    meta: MetaResponse = Field(..., description="Response metadata")


class LLMSuccessResponse(BaseModel):
    """LLM-specific success response format with typed data."""

    success: Literal[True] = Field(True, description="Success flag")
    data: LLMResponseData = Field(..., description="LLM response data")
    meta: MetaResponse = Field(..., description="Response metadata")


class ErrorResponse(BaseModel):
    """Error response format."""

    success: Literal[False] = Field(False, description="Success flag")
    error: ErrorDetail = Field(..., description="Error details")
    meta: MetaResponse = Field(..., description="Response metadata")


# Type alias for proxy response (union of success and error)
ProxyResponse = Union[SuccessResponse, ErrorResponse]
LLMProxyResponse = Union[LLMSuccessResponse, ErrorResponse]
