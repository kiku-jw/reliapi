"""Health check and monitoring endpoints.

This module provides:
- GET /health - Basic health check
- GET /healthz - Kubernetes-style health check
- GET /readyz - Readiness check
- GET /livez - Liveness check
- GET /metrics - Prometheus metrics
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from reliapi import __version__
from reliapi.app.dependencies import get_app_state

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    """Health check response model."""

    status: str
    version: str = __version__


class StatusResponse(BaseModel):
    """Simple status response model."""

    status: str


def _check_health_rate_limit(request: Request, prefix: str) -> None:
    """Check rate limit for health endpoints.

    Args:
        request: FastAPI request
        prefix: Rate limit prefix (e.g., 'healthz', 'metrics')

    Raises:
        HTTPException: If rate limit exceeded
    """
    state = get_app_state()

    if not state.rate_limiter:
        return

    client_ip = request.client.host if request.client else "unknown"
    limit = 10 if prefix == "metrics" else 20

    allowed, error = state.rate_limiter.check_ip_rate_limit(
        client_ip, limit_per_minute=limit, prefix=prefix
    )

    if not allowed:
        if prefix == "metrics":
            logger.warning(f"Rate limit exceeded for /metrics endpoint: IP={client_ip}")

        raise HTTPException(
            status_code=429,
            detail={
                "type": "rate_limit_error",
                "code": error,
                "message": f"Rate limit exceeded for {prefix} endpoint.",
            },
        )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Basic health check endpoint for load balancers and monitoring."""
    return HealthResponse(status="ok")


@router.get("/healthz", response_model=StatusResponse)
async def healthz(request: Request) -> StatusResponse:
    """Kubernetes-style health check endpoint with optional rate limiting."""
    _check_health_rate_limit(request, "healthz")
    return StatusResponse(status="healthy")


@router.get("/readyz", response_model=StatusResponse)
async def readyz(request: Request) -> StatusResponse:
    """Readiness check endpoint with optional rate limiting."""
    _check_health_rate_limit(request, "readyz")
    return StatusResponse(status="ready")


@router.get("/livez", response_model=StatusResponse)
async def livez(request: Request) -> StatusResponse:
    """Liveness check endpoint with optional rate limiting."""
    _check_health_rate_limit(request, "livez")
    return StatusResponse(status="alive")


@router.get("/metrics")
async def metrics(request: Request) -> Response:
    """Prometheus metrics endpoint with rate limiting."""
    _check_health_rate_limit(request, "metrics")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
