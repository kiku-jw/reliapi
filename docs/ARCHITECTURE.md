# ReliAPI Architecture

This document describes the high-level architecture and design decisions of ReliAPI.

## Overview

ReliAPI is a reliability layer for HTTP and LLM API calls. It provides:

- **Retries** with exponential backoff
- **Circuit breaker** to prevent cascading failures
- **Caching** for GET/HEAD requests and LLM responses
- **Idempotency** with request coalescing
- **Rate limiting** with abuse detection
- **Budget caps** for LLM cost control
- **Multi-tenancy** with RapidAPI integration

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Client Request                              │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Application                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │   Health    │  │    Proxy    │  │  RapidAPI   │  │  Business   │   │
│  │   Routes    │  │   Routes    │  │   Routes    │  │   Routes    │   │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘   │
│                            │                                             │
│                            ▼                                             │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                      Dependencies Layer                            │  │
│  │  • verify_api_key()     • detect_client_profile()                 │  │
│  │  • AppState             • Configuration validation                 │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           Services Layer                                │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────┐  │
│  │      handle_http_proxy      │  │       handle_llm_proxy          │  │
│  │  • Target resolution        │  │  • Provider selection           │  │
│  │  • Request building         │  │  • Model routing                │  │
│  │  • Response processing      │  │  • Cost estimation              │  │
│  └─────────────────────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                          Core Reliability Layer                         │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐│
│  │   Cache   │ │  Circuit  │ │   Retry   │ │   Rate    │ │Idempotency││
│  │           │ │  Breaker  │ │   Engine  │ │  Limiter  │ │  Manager  ││
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘│
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐│
│  │  Key Pool │ │   Rate    │ │  Client   │ │   Cost    │ │  Security ││
│  │  Manager  │ │ Scheduler │ │ Profiles  │ │ Estimator │ │  Manager  ││
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘│
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            Adapters Layer                               │
│  ┌─────────────────────────────┐  ┌─────────────────────────────────┐  │
│  │      LLM Adapters           │  │      HTTP Client Adapter        │  │
│  │  • OpenAI                   │  │  • Universal HTTP client        │  │
│  │  • Anthropic                │  │  • Connection pooling           │  │
│  │  • Mistral                  │  │  • Timeout handling             │  │
│  └─────────────────────────────┘  └─────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         External Services                               │
│  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌───────────┐              │
│  │   Redis   │ │  OpenAI   │ │ Anthropic │ │  Mistral  │ │  Target   ││
│  │  (State)  │ │   API     │ │    API    │ │    API    │ │   APIs    ││
│  └───────────┘ └───────────┘ └───────────┘ └───────────┘ └───────────┘│
└─────────────────────────────────────────────────────────────────────────┘
```

## Directory Structure

```
reliapi/
├── reliapi/                  # Importable package
│   ├── app/                  # FastAPI application
│   ├── core/                 # Core reliability components
│   ├── adapters/             # Provider adapters
│   ├── config/               # Configuration
│   ├── integrations/         # External integrations
│   └── metrics/              # Observability
├── cli/                      # CLI package
├── action/                   # GitHub Action
├── scripts/                  # OpenAPI / SDK / release helpers
├── sdk/                      # SDK templates and generated artifacts
└── tests/                    # Test suite
```

## Core Components

### 1. Cache (`reliapi/core/cache.py`)

Redis-backed TTL cache with multi-tenant isolation.

**Key features:**
- GET/HEAD request caching
- Conditional POST caching (explicit opt-in)
- Tenant-isolated cache keys
- Graceful degradation if Redis unavailable

**Cache key format:**
```
{prefix}:tenant:{tenant}:cache:{hash}
```

### 2. Circuit Breaker (`reliapi/core/circuit_breaker.py`)

Prevents cascading failures using the circuit breaker pattern.

**States:**
- **Closed**: Normal operation, requests pass through
- **Open**: Failures exceeded threshold, requests rejected
- **Half-Open**: Testing if service recovered

**Configuration:**
```yaml
circuit_breaker:
  failure_threshold: 5
  recovery_timeout: 30
```

### 3. Retry Engine (`reliapi/core/retry.py`)

Exponential backoff retry with customizable policies.

**Retry matrix by error type:**
```yaml
retry:
  429: {retries: 5, backoff: 2.0}  # Rate limit
  5xx: {retries: 3, backoff: 1.5}  # Server errors
  timeout: {retries: 3, backoff: 2.0}
  network: {retries: 2, backoff: 1.0}
```

### 4. Rate Limiter (`reliapi/core/rate_limiter.py`)

Multi-layer rate limiting with abuse detection.

**Layers:**
1. IP-based rate limiting
2. Account burst limiting
3. Fingerprint-based identity
4. Anomaly detection
5. Auto-ban for repeated violations

### 5. Idempotency Manager (`reliapi/core/idempotency.py`)

Request coalescing with Redis SETNX.

**Features:**
- Duplicate request detection
- In-progress locking
- Result caching with TTL

### 6. Key Pool Manager (`reliapi/core/key_pool.py`)

Multi-key rotation for LLM providers.

**Features:**
- Round-robin key selection
- QPS limit per key
- Automatic failover on rate limits

## Request Flow

### HTTP Proxy Request

```
1. Request arrives at POST /proxy/http
2. verify_api_key() - Authenticate and resolve tenant/tier
3. Rate limiting checks (IP, burst, fingerprint)
4. Check idempotency cache
5. Check response cache
6. Check circuit breaker state
7. Execute request with retry logic
8. Store in cache if cacheable
9. Record metrics
10. Return response
```

### LLM Proxy Request

```
1. Request arrives at POST /proxy/llm
2. verify_api_key() - Authenticate and resolve tenant/tier
3. Free tier restriction checks (model, features)
4. Rate limiting and abuse detection
5. RouteLLM routing decision (if configured)
6. Check idempotency cache
7. Check response cache
8. Cost estimation and budget check
9. Select provider key from pool
10. Execute LLM request with retry
11. Calculate actual cost
12. Store in cache
13. Record metrics and usage
14. Return response
```

## Design Decisions

### 1. Graceful Degradation

All Redis-dependent features degrade gracefully:
```python
try:
    self.client = redis.from_url(redis_url)
    self.enabled = True
except Exception:
    self.client = None
    self.enabled = False
```

### 2. Adapter Pattern for LLM Providers

Unified interface for multiple LLM providers:
```python
class BaseLLMAdapter(ABC):
    @abstractmethod
    async def complete(self, messages, model, **kwargs) -> LLMResponse:
        pass
```

### 3. Factory Pattern for Provider Selection

```python
def create_adapter(provider: str) -> BaseLLMAdapter:
    adapters = {
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
        "mistral": MistralAdapter,
    }
    return adapters[provider]()
```

### 4. AppState for Dependency Injection

Centralized state management:
```python
@dataclass
class AppState:
    config_loader: Optional[ConfigLoader] = None
    cache: Optional[Cache] = None
    rate_limiter: Optional[RateLimiter] = None
    # ... other components
```

### 5. Configuration-Driven Behavior

All behavior configurable via YAML:
```yaml
targets:
  my_api:
    base_url: https://api.example.com
    cache:
      ttl: 300
    retry:
      max_attempts: 3
    circuit_breaker:
      failure_threshold: 5
```

## Observability

### Prometheus Metrics

- `reliapi_requests_total` - Total requests by target/status
- `reliapi_request_duration_seconds` - Request latency histogram
- `reliapi_cache_hits_total` - Cache hit/miss counts
- `reliapi_circuit_breaker_state` - Circuit breaker state gauge
- `reliapi_llm_cost_usd` - LLM cost histogram

### Structured Logging

JSON-formatted logs for easy aggregation:
```json
{
  "timestamp": "2025-01-15T10:30:00Z",
  "level": "INFO",
  "request_id": "req_abc123",
  "target": "openai",
  "duration_ms": 150,
  "cache_hit": false
}
```

## Security Considerations

1. **API Key Validation** - Format validation before use
2. **Rate Limiting** - Multi-layer protection against abuse
3. **Fingerprinting** - Detect account sharing/abuse
4. **Auto-ban** - Automatic blocking of repeat offenders
5. **CORS** - Configurable origin restrictions
6. **Non-root Docker** - Container runs as unprivileged user

## Deployment

### Docker

```bash
docker build -t reliapi .
docker run -p 8000:8000 -e REDIS_URL=redis://redis:6379 reliapi
```

### Docker Compose

```yaml
services:
  reliapi:
    build: .
    ports:
      - "8000:8000"
    depends_on:
      redis:
        condition: service_healthy
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
```

## Performance Considerations

1. **Connection Pooling** - httpx maintains connection pools
2. **Async I/O** - All I/O operations are async
3. **Memory Management** - Rate scheduler cleanup task
4. **Redis Pipelining** - Batch operations where possible
5. **Lazy Loading** - Routes loaded on demand
