"""Circuit breaker implementation - universal for any upstream."""
import threading
import time
from collections import defaultdict
from typing import Dict


class CircuitBreaker:
    """Simple circuit breaker with failure counting and TTL.
    
    Universal implementation that works for any upstream identifier (provider, route, etc.).
    
    Note: Uses threading.Lock for thread-safety in async contexts where multiple
    concurrent requests may update the same upstream's failure count.
    """

    def __init__(self, failures_to_open: int = 3, open_ttl_s: int = 60):
        """
        Args:
            failures_to_open: Number of consecutive failures before opening circuit
            open_ttl_s: Time in seconds before attempting to close circuit again
        """
        self.failures_to_open = failures_to_open
        self.open_ttl_s = open_ttl_s
        self.failure_counts: Dict[str, int] = defaultdict(int)
        self.opened_at: Dict[str, float] = {}
        self._lock = threading.Lock()  # Thread-safe lock for async context

    def record_success(self, upstream: str) -> None:
        """Reset failure count on success."""
        with self._lock:
            self.failure_counts[upstream] = 0
            if upstream in self.opened_at:
                del self.opened_at[upstream]

    def record_failure(self, upstream: str) -> None:
        """Record a failure and check if circuit should open."""
        with self._lock:
            self.failure_counts[upstream] += 1
            if self.failure_counts[upstream] >= self.failures_to_open:
                self.opened_at[upstream] = time.time()

    def is_open(self, upstream: str) -> bool:
        """Check if circuit is open for upstream."""
        with self._lock:
            if upstream not in self.opened_at:
                return False

            opened_time = self.opened_at[upstream]
            if time.time() - opened_time >= self.open_ttl_s:
                # Auto-close after TTL
                self.failure_counts[upstream] = 0
                del self.opened_at[upstream]
                return False

            return True

    def get_state(self, upstream: str) -> str:
        """Get circuit state: 'closed', 'open', or 'half-open'."""
        with self._lock:
            # Check if circuit is open (inline logic to avoid deadlock)
            if upstream in self.opened_at:
                opened_time = self.opened_at[upstream]
                if time.time() - opened_time >= self.open_ttl_s:
                    # Auto-close after TTL
                    self.failure_counts[upstream] = 0
                    del self.opened_at[upstream]
                else:
                    return "open"
            
            if self.failure_counts[upstream] > 0:
                return "half-open"
            return "closed"


