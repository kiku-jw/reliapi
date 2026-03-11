"""Universal HTTP client with retries and circuit breaker."""
import time
from typing import Any, Dict, Optional

import httpx

from reliapi.core.circuit_breaker import CircuitBreaker
from reliapi.core.retry import RetryEngine, RetryMatrix


class UpstreamHTTPClient:
    """HTTP client for upstream APIs with retries and circuit breaker."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 30.0,
        retry_matrix: Optional[Dict[str, RetryMatrix]] = None,
        circuit_breaker: Optional[CircuitBreaker] = None,
        auth: Optional[Dict[str, Any]] = None,
    ):
        """
        Args:
            base_url: Base URL for upstream
            timeout_s: Request timeout in seconds
            retry_matrix: Retry policies by error class
            circuit_breaker: Circuit breaker instance
            auth: Authentication config (type, header, prefix, etc.)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retry_engine = RetryEngine(retry_matrix)
        self.circuit_breaker = circuit_breaker or CircuitBreaker()
        self.auth = auth or {}
        
        # Create HTTP client with connection pooling
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_s, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )

    def _prepare_headers(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Prepare headers with authentication."""
        result = headers.copy() if headers else {}
        
        # Add auth header if configured
        if self.auth.get("type") == "api_key":
            header_name = self.auth.get("header", "Authorization")
            prefix = self.auth.get("prefix", "")
            api_key = self.auth.get("api_key", "")
            if api_key:
                result[header_name] = f"{prefix}{api_key}"
        
        return result

    async def request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        """
        Make HTTP request with retries and circuit breaker.
        
        Args:
            method: HTTP method
            path: Path (will be appended to base_url)
            headers: Request headers
            body: Request body
            params: Query parameters
            
        Returns:
            HTTP response
            
        Raises:
            httpx.HTTPError: On network/HTTP errors
        """
        upstream_id = f"{self.base_url}"
        
        # Check circuit breaker
        if self.circuit_breaker.is_open(upstream_id):
            raise httpx.HTTPError("Circuit breaker is open")

        prepared_headers = self._prepare_headers(headers)
        url = f"{self.base_url}{path}"

        async def _make_request():
            try:
                response = await self.client.request(
                    method=method.upper(),
                    url=url,
                    headers=prepared_headers,
                    content=body,
                    params=params,
                )
                
                # Record success/failure
                if response.is_success:
                    self.circuit_breaker.record_success(upstream_id)
                elif response.status_code >= 500:
                    self.circuit_breaker.record_failure(upstream_id)
                    raise httpx.HTTPStatusError(
                        f"Server error: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                elif response.status_code == 429:
                    self.circuit_breaker.record_failure(upstream_id)
                    raise httpx.HTTPStatusError(
                        f"Rate limited: {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                
                return response
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                self.circuit_breaker.record_failure(upstream_id)
                raise

        # Execute with retries
        response = await self.retry_engine.execute(_make_request)
        return response

    async def close(self):
        """Close HTTP client."""
        await self.client.aclose()


