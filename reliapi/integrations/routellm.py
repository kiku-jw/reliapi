"""RouteLLM integration for intelligent model routing.

RouteLLM is a routing system that helps select the best LLM provider/model
based on request characteristics. ReliAPI integrates with RouteLLM by:

1. Respecting routing decisions from RouteLLM headers
2. Applying reliability features (key pool, rate smoothing, retries)
3. Exposing correlation IDs for tracing

Headers:
- X-RouteLLM-Provider: Override provider selection (e.g., "openai", "anthropic")
- X-RouteLLM-Model: Override model selection (e.g., "gpt-4o", "claude-3-opus")
- X-RouteLLM-Decision-ID: Correlation ID from RouteLLM for tracing
- X-RouteLLM-Route-Name: Name of the routing rule that was applied
- X-RouteLLM-Reason: Human-readable reason for the routing decision
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# RouteLLM header names
ROUTELLM_PROVIDER_HEADER = "X-RouteLLM-Provider"
ROUTELLM_MODEL_HEADER = "X-RouteLLM-Model"
ROUTELLM_DECISION_ID_HEADER = "X-RouteLLM-Decision-ID"
ROUTELLM_ROUTE_NAME_HEADER = "X-RouteLLM-Route-Name"
ROUTELLM_REASON_HEADER = "X-RouteLLM-Reason"

# Response headers
RELIAPI_PROVIDER_HEADER = "X-ReliAPI-Provider"
RELIAPI_MODEL_HEADER = "X-ReliAPI-Model"
RELIAPI_DECISION_ID_HEADER = "X-ReliAPI-Decision-ID"


@dataclass
class RouteLLMDecision:
    """Routing decision from RouteLLM headers."""
    provider: Optional[str] = None
    model: Optional[str] = None
    decision_id: Optional[str] = None
    route_name: Optional[str] = None
    reason: Optional[str] = None
    
    @property
    def has_override(self) -> bool:
        """Check if there's any routing override."""
        return bool(self.provider or self.model)
    
    def to_response_headers(self) -> Dict[str, str]:
        """Generate response headers for correlation."""
        headers = {}
        if self.provider:
            headers[RELIAPI_PROVIDER_HEADER] = self.provider
        if self.model:
            headers[RELIAPI_MODEL_HEADER] = self.model
        if self.decision_id:
            headers[RELIAPI_DECISION_ID_HEADER] = self.decision_id
        return headers
    
    def to_log_context(self) -> Dict[str, Any]:
        """Generate context for structured logging."""
        return {
            k: v for k, v in {
                "routellm_provider": self.provider,
                "routellm_model": self.model,
                "routellm_decision_id": self.decision_id,
                "routellm_route_name": self.route_name,
                "routellm_reason": self.reason,
            }.items() if v
        }


def extract_routellm_decision(headers: Dict[str, str]) -> Optional[RouteLLMDecision]:
    """
    Extract RouteLLM routing decision from request headers.
    
    Args:
        headers: Request headers dict
        
    Returns:
        RouteLLMDecision if any RouteLLM headers present, None otherwise
    """
    # Handle case-insensitive header lookup
    normalized_headers = {k.lower(): v for k, v in headers.items()}
    
    provider = normalized_headers.get(ROUTELLM_PROVIDER_HEADER.lower())
    model = normalized_headers.get(ROUTELLM_MODEL_HEADER.lower())
    decision_id = normalized_headers.get(ROUTELLM_DECISION_ID_HEADER.lower())
    route_name = normalized_headers.get(ROUTELLM_ROUTE_NAME_HEADER.lower())
    reason = normalized_headers.get(ROUTELLM_REASON_HEADER.lower())
    
    # Return None if no RouteLLM headers present
    if not any([provider, model, decision_id, route_name, reason]):
        return None
    
    decision = RouteLLMDecision(
        provider=provider,
        model=model,
        decision_id=decision_id,
        route_name=route_name,
        reason=reason,
    )
    
    logger.debug(f"RouteLLM decision extracted: {decision.to_log_context()}")
    return decision


def apply_routellm_overrides(
    target_name: str,
    model: Optional[str],
    targets: Dict[str, Dict],
    decision: Optional[RouteLLMDecision],
) -> tuple[str, Optional[str]]:
    """
    Apply RouteLLM routing overrides to target and model selection.
    
    Args:
        target_name: Original target name from request
        model: Original model from request
        targets: Available targets configuration
        decision: RouteLLM routing decision
        
    Returns:
        Tuple of (resolved_target_name, resolved_model)
    """
    if not decision or not decision.has_override:
        return target_name, model
    
    resolved_target = target_name
    resolved_model = model
    
    # Override provider/target if specified
    if decision.provider:
        # Try to find a target matching the provider
        for name, config in targets.items():
            llm_config = config.get("llm", {})
            if llm_config.get("provider", "").lower() == decision.provider.lower():
                resolved_target = name
                logger.info(f"RouteLLM override: target {target_name} -> {resolved_target} (provider: {decision.provider})")
                break
        else:
            # Try direct target name match
            if decision.provider in targets:
                resolved_target = decision.provider
                logger.info(f"RouteLLM override: target {target_name} -> {resolved_target}")
    
    # Override model if specified
    if decision.model:
        resolved_model = decision.model
        logger.info(f"RouteLLM override: model {model} -> {resolved_model}")
    
    return resolved_target, resolved_model


def get_provider_from_target(target_name: str, targets: Dict[str, Dict]) -> Optional[str]:
    """
    Get provider name from target configuration.
    
    Args:
        target_name: Target name
        targets: Targets configuration
        
    Returns:
        Provider name or None
    """
    target_config = targets.get(target_name)
    if not target_config:
        return None
    
    llm_config = target_config.get("llm", {})
    return llm_config.get("provider")


class RouteLLMMetrics:
    """Metrics collector for RouteLLM routing decisions."""
    
    def __init__(self):
        self._decisions_total: Dict[str, int] = {}
        self._overrides_applied: Dict[str, int] = {}
    
    def record_decision(self, decision: Optional[RouteLLMDecision]):
        """Record a routing decision."""
        if not decision:
            return
        
        route_name = decision.route_name or "unknown"
        self._decisions_total[route_name] = self._decisions_total.get(route_name, 0) + 1
        
        if decision.has_override:
            key = f"{decision.provider or 'default'}:{decision.model or 'default'}"
            self._overrides_applied[key] = self._overrides_applied.get(key, 0) + 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        return {
            "decisions_total": dict(self._decisions_total),
            "overrides_applied": dict(self._overrides_applied),
        }


# Global metrics instance
routellm_metrics = RouteLLMMetrics()


