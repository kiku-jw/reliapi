"""Error codes and status normalization for ReliAPI."""
from enum import Enum
from typing import Optional


class ErrorCode(str, Enum):
    """Normalized error codes for ReliAPI.
    
    All error codes must be one of these values.
    Used in responses, logs, and metrics.
    """
    # Client errors (4xx)
    UNAUTHORIZED = "UNAUTHORIZED"
    BAD_REQUEST = "BAD_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    STREAM_ALREADY_IN_PROGRESS = "STREAM_ALREADY_IN_PROGRESS"
    STREAM_ALREADY_COMPLETED = "STREAM_ALREADY_COMPLETED"
    STREAMING_UNSUPPORTED = "STREAMING_UNSUPPORTED"
    RATE_LIMIT_RELIAPI = "RATE_LIMIT_RELIAPI"
    
    # Upstream errors (from target APIs)
    SERVER_ERROR = "SERVER_ERROR"  # 5xx from upstream
    CLIENT_ERROR = "CLIENT_ERROR"  # 4xx from upstream
    NETWORK_ERROR = "NETWORK_ERROR"  # Network/timeout
    PROVIDER_ERROR = "PROVIDER_ERROR"  # Generic provider error
    UPSTREAM_STREAM_INTERRUPTED = "UPSTREAM_STREAM_INTERRUPTED"
    
    # Budget errors
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    
    # Configuration errors
    INVALID_TARGET = "INVALID_TARGET"
    UNKNOWN_PROVIDER = "UNKNOWN_PROVIDER"
    ADAPTER_NOT_FOUND = "ADAPTER_NOT_FOUND"
    
    # Internal errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    
    @classmethod
    def from_http_status(cls, status_code: int) -> "ErrorCode":
        """Map HTTP status code to error code."""
        if status_code >= 500:
            return cls.SERVER_ERROR
        elif status_code == 401:
            return cls.UNAUTHORIZED
        elif status_code == 404:
            return cls.NOT_FOUND
        elif status_code == 409:
            return cls.IDEMPOTENCY_CONFLICT
        elif status_code >= 400:
            return cls.CLIENT_ERROR
        else:
            return cls.INTERNAL_ERROR
    
    @classmethod
    def normalize(cls, code: Optional[str]) -> Optional[str]:
        """Normalize error code string to enum value.
        
        Returns None if code is None or not a valid enum value.
        """
        if not code:
            return None
        try:
            return cls(code).value
        except ValueError:
            # Unknown code - log warning but return as-is for backward compatibility
            # In production, should log and map to INTERNAL_ERROR
            return code


class UpstreamStatus(str, Enum):
    """Normalized upstream status codes for metrics.
    
    Upstream status codes are normalized to reduce Prometheus cardinality.
    Actual status codes are preserved in logs and response details.
    """
    # Success
    OK = "200"
    
    # Client errors (4xx)
    BAD_REQUEST = "400"
    UNAUTHORIZED = "401"
    FORBIDDEN = "403"
    NOT_FOUND = "404"
    CONFLICT = "409"
    TOO_MANY_REQUESTS = "429"
    CLIENT_ERROR_OTHER = "4xx"  # Other 4xx
    
    # Server errors (5xx)
    INTERNAL_SERVER_ERROR = "500"
    BAD_GATEWAY = "502"
    SERVICE_UNAVAILABLE = "503"
    GATEWAY_TIMEOUT = "504"
    SERVER_ERROR_OTHER = "5xx"  # Other 5xx
    
    # Network/timeout
    NETWORK_ERROR = "network_error"
    TIMEOUT = "timeout"
    
    # Unknown
    UNKNOWN = "unknown"
    
    @classmethod
    def normalize(cls, status_code: Optional[int]) -> str:
        """Normalize HTTP status code to enum value.
        
        Args:
            status_code: HTTP status code or None
            
        Returns:
            Normalized status string for metrics
        """
        if status_code is None:
            return cls.UNKNOWN.value
        
        if status_code == 200:
            return cls.OK.value
        elif status_code == 400:
            return cls.BAD_REQUEST.value
        elif status_code == 401:
            return cls.UNAUTHORIZED.value
        elif status_code == 403:
            return cls.FORBIDDEN.value
        elif status_code == 404:
            return cls.NOT_FOUND.value
        elif status_code == 409:
            return cls.CONFLICT.value
        elif status_code == 429:
            return cls.TOO_MANY_REQUESTS.value
        elif 400 <= status_code < 500:
            return cls.CLIENT_ERROR_OTHER.value
        elif status_code == 500:
            return cls.INTERNAL_SERVER_ERROR.value
        elif status_code == 502:
            return cls.BAD_GATEWAY.value
        elif status_code == 503:
            return cls.SERVICE_UNAVAILABLE.value
        elif status_code == 504:
            return cls.GATEWAY_TIMEOUT.value
        elif 500 <= status_code < 600:
            return cls.SERVER_ERROR_OTHER.value
        else:
            return cls.UNKNOWN.value

