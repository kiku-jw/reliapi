"""Retry engine with exponential backoff and jitter."""
import asyncio
import math
import random
import time
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Dict, Optional, TypeVar

T = TypeVar("T")


class RetryMatrix:
    """Retry policy matrix for different error classes."""

    def __init__(
        self,
        attempts: int = 3,
        backoff: str = "exp-jitter",
        base_s: float = 1.0,
        max_s: float = 60.0,
    ):
        """
        Args:
            attempts: Maximum number of retry attempts
            backoff: Backoff strategy ("exp-jitter", "exp", "linear")
            base_s: Base delay in seconds
            max_s: Maximum delay in seconds
        """
        self.attempts = attempts
        self.backoff = backoff
        self.base_s = base_s
        self.max_s = max_s

    def get_delay(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Calculate delay for retry attempt.

        Args:
            attempt: Retry attempt number (1-based)
            retry_after: Retry-After header value in seconds (if present)

        Returns:
            Delay in seconds
        """
        # If Retry-After header is present, use it (capped at max_s)
        if retry_after is not None:
            return min(retry_after, self.max_s)

        # Otherwise use configured backoff strategy
        if self.backoff == "exp-jitter":
            delay = self.base_s * (2 ** (attempt - 1))
            jitter = random.uniform(0, delay * 0.3)
            delay = min(delay + jitter, self.max_s)
        elif self.backoff == "exp":
            delay = min(self.base_s * (2 ** (attempt - 1)), self.max_s)
        elif self.backoff == "linear":
            delay = min(self.base_s * attempt, self.max_s)
        else:
            delay = self.base_s

        return delay


class RetryEngine:
    """Universal retry engine for HTTP requests."""

    def __init__(self, matrix: Optional[Dict[str, RetryMatrix]] = None):
        """
        Args:
            matrix: Dictionary mapping error classes to retry policies
                   Keys: "429", "5xx", "net", "timeout"
        """
        self.matrix = matrix or {
            "429": RetryMatrix(attempts=3, backoff="exp-jitter", base_s=1.0),
            "5xx": RetryMatrix(attempts=2, backoff="exp-jitter", base_s=1.0),
            "net": RetryMatrix(attempts=2, backoff="exp-jitter", base_s=1.0),
            "timeout": RetryMatrix(attempts=2, backoff="exp-jitter", base_s=1.0),
        }

    @staticmethod
    def _parse_retry_after_header(retry_after_header: str) -> Optional[float]:
        """Parse Retry-After header value.

        Supports both integer-second and HTTP-date formats. For HTTP-date values
        we round up to the next second to avoid retrying too early because of
        second-level timestamp precision.
        """
        try:
            return float(retry_after_header)
        except ValueError:
            try:
                retry_after_dt = parsedate_to_datetime(retry_after_header)
                delay = retry_after_dt.timestamp() - time.time()
                if delay <= 0:
                    return 0.0
                return float(math.ceil(delay))
            except (TypeError, ValueError, OverflowError):
                return None

    def _classify_error(self, status_code: Optional[int], error: Optional[Exception]) -> str:
        """Classify error for retry policy selection."""
        if error:
            error_name = type(error).__name__.lower()
            if "timeout" in error_name or "timedout" in error_name:
                return "timeout"
            if "connection" in error_name or "network" in error_name:
                return "net"

        if status_code:
            if status_code == 429:
                return "429"
            if 500 <= status_code < 600:
                return "5xx"

        # Default: don't retry
        return "no-retry"

    async def execute(
        self,
        func: Callable[[], Any],
        error_classifier: Optional[Callable[[Optional[int], Optional[Exception]], str]] = None,
        get_retry_after: Optional[Callable[[Exception], Optional[float]]] = None,
    ) -> T:
        """
        Execute function with retries.

        Args:
            func: Async function to execute (should return (status_code, result) or raise)
            error_classifier: Optional custom error classifier
            get_retry_after: Optional function to extract Retry-After from exception

        Returns:
            Result from function

        Raises:
            Last exception if all retries exhausted
        """
        last_error: Optional[Exception] = None
        last_status: Optional[int] = None

        for attempt in range(1, 10):  # Max 10 attempts across all policies
            try:
                result = await func()
                return result
            except Exception as e:
                last_error = e
                # Try to extract status code from error if possible
                status_code = getattr(e, "status_code", None)
                last_status = status_code

                # Classify error
                if error_classifier:
                    error_class = error_classifier(status_code, e)
                else:
                    error_class = self._classify_error(status_code, e)

                # Get retry policy
                policy = self.matrix.get(error_class)
                if not policy or attempt >= policy.attempts:
                    raise

                # Extract Retry-After if available
                retry_after = None
                if get_retry_after:
                    retry_after = get_retry_after(e)

                if hasattr(e, "response") and hasattr(e.response, "headers"):
                    retry_after_str = e.response.headers.get("Retry-After")
                    if retry_after_str:
                        parsed_retry_after = self._parse_retry_after_header(retry_after_str)
                        if parsed_retry_after is not None:
                            retry_after = (
                                parsed_retry_after
                                if retry_after is None
                                else max(retry_after, parsed_retry_after)
                            )

                # Calculate delay
                delay = policy.get_delay(attempt, retry_after=retry_after)
                await asyncio.sleep(delay)

        # All retries exhausted
        if last_error:
            raise last_error
        raise RuntimeError("Retry exhausted without result")
