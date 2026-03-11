"""ReliAPI FastAPI application - minimal reliability layer.

This is the main application module that:
- Initializes the FastAPI application
- Configures middleware (CORS, exception handling)
- Registers all route handlers
- Manages application lifespan (startup/shutdown)
"""
import logging
import os
import traceback
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from reliapi import __version__
from reliapi.app.dependencies import (
    ConfigValidationError,
    get_app_state,
    init_client_profile_manager,
    init_key_pool_manager,
    validate_startup_config,
)
from reliapi.config.loader import ConfigLoader
from reliapi.core.cache import Cache
from reliapi.core.idempotency import IdempotencyManager
from reliapi.core.rate_limiter import RateLimiter
from reliapi.core.rate_scheduler import RateScheduler
from reliapi.integrations.rapidapi import RapidAPIClient
from reliapi.integrations.rapidapi_tenant import RapidAPITenantManager

# Configure structured JSON logging
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager.

    Handles startup initialization and shutdown cleanup for:
    - Configuration loading and validation
    - Redis connections (cache, idempotency, rate limiting)
    - RapidAPI integration
    - Key pool and rate scheduler
    - Client profile management
    """
    state = get_app_state()

    # Startup
    config_path = os.getenv("RELIAPI_CONFIG_PATH", os.getenv("RELIAPI_CONFIG", "config.yaml"))
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    logger.info(f"Loading configuration from {config_path}")
    state.config_loader = ConfigLoader(config_path)
    state.config_loader.load()
    state.targets = state.config_loader.get_targets()

    # Validate configuration (fail fast on invalid config)
    strict_validation = os.getenv("RELIAPI_STRICT_CONFIG", "true").lower() == "true"
    try:
        validation_warnings = validate_startup_config(state.config_loader, strict=strict_validation)
        if validation_warnings:
            logger.info(f"Configuration loaded with {len(validation_warnings)} warning(s)")
    except ConfigValidationError as e:
        logger.critical(f"Configuration validation failed: {e}")
        raise

    logger.info(f"Initializing Redis connection: {redis_url}")
    state.cache = Cache(redis_url, key_prefix="reliapi")
    state.idempotency = IdempotencyManager(redis_url, key_prefix="reliapi")
    state.rate_limiter = RateLimiter(redis_url, key_prefix="reliapi")

    # Initialize RapidAPI client
    state.rapidapi_client = RapidAPIClient(
        redis_url=redis_url,
        key_prefix="reliapi",
    )
    logger.info("RapidAPI client initialized")

    # Initialize RapidAPI tenant manager
    if state.cache and state.cache.client:
        state.rapidapi_tenant_manager = RapidAPITenantManager(
            redis_client=state.cache.client,
            key_prefix="reliapi",
        )
        logger.info("RapidAPI tenant manager initialized")

    # Initialize key pool manager
    state.key_pool_manager = init_key_pool_manager(state.config_loader)
    if state.key_pool_manager:
        logger.info("Key pool manager initialized")
    else:
        logger.info("No key pools configured, using targets.auth")

    # Initialize rate scheduler with memory management
    state.rate_scheduler = RateScheduler(
        max_buckets=1000,
        bucket_ttl_seconds=3600,
        cleanup_interval_seconds=300,
    )
    await state.rate_scheduler.start_cleanup_task()
    logger.info("Rate scheduler initialized with memory management")

    # Initialize client profile manager
    state.client_profile_manager = init_client_profile_manager(state.config_loader)
    logger.info("Client profile manager initialized")

    logger.info(f"ReliAPI started with {len(state.targets)} targets")

    yield

    # Shutdown
    logger.info("Shutting down ReliAPI...")
    if state.rate_scheduler:
        await state.rate_scheduler.stop_cleanup_task()
    if state.rapidapi_client:
        await state.rapidapi_client.close()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    app = FastAPI(
        title="ReliAPI",
        version=__version__,
        description=(
            "ReliAPI is a small LLM reliability layer for HTTP and LLM calls: "
            "retries, circuit breaker, cache, idempotency, and budget caps. "
            "Idempotent LLM proxy with predictable AI costs. "
            "Self-hosted AI gateway focused on reliability, not features."
        ),
        lifespan=lifespan,
    )

    # Configure CORS middleware
    _configure_cors(app)

    # Register exception handlers
    _register_exception_handlers(app)

    # Register routes
    _register_routes(app)

    return app


def _configure_cors(app: FastAPI) -> None:
    """Configure CORS middleware with production security."""
    cors_origins_env = os.getenv("CORS_ORIGINS", "*")
    is_production = os.getenv("ENVIRONMENT", "").lower() == "production"

    if cors_origins_env == "*":
        if is_production:
            logger.warning(
                "SECURITY WARNING: CORS_ORIGINS is set to '*' in production. "
                "This allows requests from any origin. "
                "Consider restricting to specific origins."
            )
        cors_origins = ["*"]
    else:
        cors_origins = _validate_cors_origins(cors_origins_env, is_production)

    if is_production:
        logger.info(f"CORS configured for production with {len(cors_origins)} allowed origin(s)")
        if len(cors_origins) > 10:
            logger.warning(
                f"Large number of CORS origins ({len(cors_origins)}), " "consider consolidating"
            )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
    )


def _validate_cors_origins(cors_origins_env: str, is_production: bool) -> List[str]:
    """Validate and filter CORS origins.

    Args:
        cors_origins_env: Comma-separated CORS origins string
        is_production: Whether running in production

    Returns:
        List of validated CORS origins
    """
    origins = [origin.strip() for origin in cors_origins_env.split(",")]
    validated_origins = []

    for origin in origins:
        if not origin:
            continue

        if origin != "*" and not (origin.startswith("http://") or origin.startswith("https://")):
            logger.warning(f"Invalid CORS origin format (skipping): {origin}")
            continue

        if is_production and origin == "*":
            logger.warning("Wildcard CORS origin '*' not recommended in production")

        validated_origins.append(origin)

    return validated_origins


def _register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers."""

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Global exception handler for unhandled errors."""
        error_details = traceback.format_exc()
        logger.error(
            f"Unhandled exception: {type(exc).__name__}: {str(exc)}\n{error_details}",
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "type": "internal_error",
                    "code": "INTERNAL_ERROR",
                    "message": f"Internal server error: {str(exc)}",
                    "retryable": True,
                    "target": None,
                    "status_code": 500,
                },
                "meta": {
                    "target": None,
                    "cache_hit": False,
                    "retries": 0,
                    "duration_ms": 0,
                    "request_id": request.headers.get("X-Request-ID", "unknown"),
                    "trace_id": request.headers.get("X-Trace-ID"),
                },
            },
        )


def _register_routes(app: FastAPI) -> None:
    """Register all route handlers."""
    # Import and register core routes
    from reliapi.app.routes import health, proxy, rapidapi

    app.include_router(health.router)

    # v1 API routes (canonical)
    app.include_router(proxy.router, prefix="/v1")
    app.include_router(rapidapi.router, prefix="/v1")

    # Legacy routes (deprecated - will be removed in 6 months)
    app.include_router(proxy.router, deprecated=True, tags=["Legacy"])
    app.include_router(rapidapi.router, deprecated=True, tags=["Legacy"])

    # Import and register business routes
    try:
        from reliapi.app.routes import (
            analytics,
            calculators,
            dashboard,
            onboarding,
            paddle,
        )

        app.include_router(paddle.router)
        app.include_router(onboarding.router)
        app.include_router(analytics.router)
        app.include_router(calculators.router)
        app.include_router(dashboard.router)

        logger.info(
            "Business routes registered: paddle, onboarding, analytics, " "calculators, dashboard"
        )
    except ImportError as e:
        logger.warning(f"Business routes not available: {e}")


# Create the application instance
app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
