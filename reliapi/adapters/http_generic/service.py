"""HTTP Generic Service - Universal REST/SSE proxy."""
import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Optional

from fastapi import Request, Response
from fastapi.responses import StreamingResponse

from reliapi.core.cache import Cache
from reliapi.core.circuit_breaker import CircuitBreaker
from reliapi.core.http_client import UpstreamHTTPClient
from reliapi.core.idempotency import IdempotencyManager
from reliapi.core.retry import RetryMatrix


class HTTPGenericService:
    """Universal HTTP proxy service with resilience features."""

    def __init__(
        self,
        redis_url: str,
        config: Dict[str, Any],
    ):
        """
        Args:
            redis_url: Redis connection URL
            config: Route configuration
        """
        self.config = config
        self.upstream_config = config.get("upstream_config", {})
        
        # Initialize core components
        self.cache = Cache(redis_url, key_prefix="reliapi")
        self.idempotency = IdempotencyManager(redis_url, key_prefix="reliapi")
        
        # Circuit breaker
        cb_config = self.upstream_config.get("circuit_breaker", {})
        self.circuit_breaker = CircuitBreaker(
            failures_to_open=cb_config.get("failures_to_open", 3),
            open_ttl_s=cb_config.get("open_ttl_s", 60),
        )
        
        # Retry matrix
        retry_config = self.upstream_config.get("retry_matrix", {})
        retry_matrix = {}
        for error_class, policy in retry_config.items():
            retry_matrix[error_class] = RetryMatrix(
                attempts=policy.get("attempts", 3),
                backoff=policy.get("backoff", "exp-jitter"),
                base_s=policy.get("base_s", 1.0),
            )
        
        # HTTP client
        self.client = UpstreamHTTPClient(
            base_url=self.upstream_config["base_url"],
            timeout_s=self.upstream_config.get("timeout_s", 30.0),
            retry_matrix=retry_matrix,
            circuit_breaker=self.circuit_breaker,
            auth=self.upstream_config.get("auth"),
        )

    async def proxy_request(
        self,
        request: Request,
        route_config: Dict[str, Any],
    ) -> Response:
        """
        Proxy HTTP request with resilience features.
        
        Args:
            request: FastAPI request
            route_config: Route configuration
            
        Returns:
            HTTP response
        """
        method = request.method
        path = request.url.path
        headers = dict(request.headers)
        body = await request.body()
        
        # Generate request ID
        request_id = f"req_{int(time.time())}_{hashlib.md5(f'{method}:{path}:{body}'.encode()).hexdigest()[:8]}"
        
        # Check cache for GET/HEAD
        cache_policy = route_config.get("cache_policy", {})
        if cache_policy.get("enabled") and method.upper() in cache_policy.get("methods", ["GET", "HEAD"]):
            cached = self.cache.get(method, path, headers, body)
            if cached:
                return Response(
                    content=cached.get("body", ""),
                    status_code=cached.get("status_code", 200),
                    headers=cached.get("headers", {}),
                )
        
        # Check idempotency for POST/PUT/PATCH
        idempotency_policy = route_config.get("idempotency", {})
        idempotency_key = None
        if idempotency_policy.get("enabled") and method.upper() in idempotency_policy.get("for_methods", ["POST"]):
            idempotency_header = idempotency_policy.get("header", "Idempotency-Key")
            idempotency_key = headers.get(idempotency_header)
            
            if idempotency_key:
                # Check for existing result
                existing_result = self.idempotency.get_result(idempotency_key)
                if existing_result:
                    return Response(
                        content=json.dumps(existing_result.get("body", {})),
                        status_code=existing_result.get("status_code", 200),
                        headers={"Content-Type": "application/json"},
                    )
                
                # Register request
                is_new, existing_id, existing_hash = self.idempotency.register_request(
                    idempotency_key, method, path, headers, body, request_id
                )
                
                if not is_new:
                    # Request in progress or conflict
                    if existing_hash != self.idempotency.make_request_hash(method, path, headers, body):
                        return Response(
                            content=json.dumps({"error": "Idempotency key conflict"}),
                            status_code=409,
                            headers={"Content-Type": "application/json"},
                        )
                    
                    # Wait for existing request (simplified - in production use proper coalescing)
                    await asyncio.sleep(0.1)
                    existing_result = self.idempotency.get_result(idempotency_key)
                    if existing_result:
                        return Response(
                            content=json.dumps(existing_result.get("body", {})),
                            status_code=existing_result.get("status_code", 200),
                            headers={"Content-Type": "application/json"},
                        )
                
                self.idempotency.mark_in_progress(idempotency_key)
        
        # Make upstream request
        try:
            start_time = time.time()
            upstream_response = await self.client.request(
                method=method,
                path=path,
                headers=headers,
                body=body if body else None,
                params=dict(request.query_params),
            )
            
            latency_ms = int((time.time() - start_time) * 1000)
            
            # Read response body
            response_body = await upstream_response.aread()
            response_status = upstream_response.status_code
            response_headers = dict(upstream_response.headers)
            
            # Normalize response
            normalized = {
                "status_code": response_status,
                "body": response_body.decode() if response_body else "",
                "headers": response_headers,
                "latency_ms": latency_ms,
            }
            
            # Store in cache if applicable
            if cache_policy.get("enabled") and method.upper() in cache_policy.get("methods", ["GET", "HEAD"]):
                if response_status < 400:  # Only cache successful responses
                    ttl = cache_policy.get("ttl_s", 3600)
                    self.cache.set(method, path, headers, body, normalized, ttl)
            
            # Store idempotency result
            if idempotency_key:
                self.idempotency.store_result(
                    idempotency_key,
                    {
                        "status_code": response_status,
                        "body": json.loads(response_body.decode()) if response_body else {},
                    },
                )
                self.idempotency.clear_in_progress(idempotency_key)
            
            # Return response
            return Response(
                content=response_body,
                status_code=response_status,
                headers=response_headers,
            )
            
        except Exception as e:
            # Clear in-progress on error
            if idempotency_key:
                self.idempotency.clear_in_progress(idempotency_key)
            
            # Return error response
            return Response(
                content=json.dumps({"error": str(e)}),
                status_code=502,
                headers={"Content-Type": "application/json"},
            )

    async def close(self):
        """Close service and cleanup."""
        await self.client.close()


