"""Structured logging for ReliAPI."""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional


class StructuredLogger:
    """Structured JSON logger for ReliAPI requests."""
    
    def __init__(self, name: str = "reliapi"):
        self.logger = logging.getLogger(name)
    
    def log_request(
        self,
        request_id: str,
        target: Optional[str],
        kind: str,  # "http" or "llm"
        stream: bool,
        path: Optional[str] = None,  # For HTTP
        model: Optional[str] = None,  # For LLM
        outcome: str = "success",  # "success" or "error"
        error_code: Optional[str] = None,
        upstream_status: Optional[int] = None,
        latency_ms: int = 0,
        cost_usd: Optional[float] = None,
        cache_hit: bool = False,
        idempotent_hit: bool = False,
        level: str = "INFO",
        tenant: Optional[str] = None,
    ):
        """Log a request summary as structured JSON.
        
        Args:
            request_id: Unique request identifier
            target: Target name from config
            kind: "http" or "llm"
            stream: Whether this was a streaming request (only for LLM)
            path: HTTP path (for HTTP requests)
            model: LLM model name (for LLM requests)
            outcome: "success" or "error"
            error_code: Error code if outcome is "error"
            upstream_status: Upstream HTTP status code
            latency_ms: Request latency in milliseconds
            cost_usd: Cost in USD (for LLM requests)
            cache_hit: Whether response was from cache
            idempotent_hit: Whether response was from idempotency cache
            level: Log level (INFO, WARNING, ERROR)
        """
        log_entry: Dict[str, Any] = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": level,
            "request_id": request_id,
            "target": target,
            "kind": kind,
            "stream": stream if kind == "llm" else False,
            "outcome": outcome,
            "latency_ms": latency_ms,
            "cache_hit": cache_hit,
            "idempotent_hit": idempotent_hit,
        }
        
        # Add tenant if provided (multi-tenant mode)
        if tenant:
            log_entry["tenant"] = tenant
        
        # Add kind-specific fields
        if kind == "http" and path:
            log_entry["path"] = path
        elif kind == "llm" and model:
            log_entry["model"] = model
        
        # Add error fields if applicable
        if outcome == "error":
            if error_code:
                log_entry["error_code"] = error_code
            if upstream_status:
                log_entry["upstream_status"] = upstream_status
        
        # Add cost for LLM requests
        if kind == "llm" and cost_usd is not None:
            log_entry["cost_usd"] = cost_usd
        
        # Log as JSON line
        log_message = json.dumps(log_entry, ensure_ascii=False)
        
        if level == "ERROR":
            self.logger.error(log_message)
        elif level == "WARNING":
            self.logger.warning(log_message)
        else:
            self.logger.info(log_message)


# Global structured logger instance
structured_logger = StructuredLogger()


def trace_context(request_id: str, target: Optional[str] = None, kind: Optional[str] = None) -> Dict[str, Any]:
    """Get current trace context for a request.
    
    Returns a dictionary with core tracing fields that can be used
    for correlation across services or for debugging.
    
    Args:
        request_id: Request ID
        target: Target name (optional)
        kind: Request kind - "http" or "llm" (optional)
    
    Returns:
        Dictionary with request_id and optional target/kind
    """
    ctx = {"request_id": request_id}
    if target:
        ctx["target"] = target
    if kind:
        ctx["kind"] = kind
    return ctx

