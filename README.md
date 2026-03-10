# ReliAPI

Reliability layer for API calls: retries, caching, dedup, circuit breakers.

[![npm version](https://badge.fury.io/js/reliapi-sdk.svg)](https://www.npmjs.com/package/reliapi-sdk)
[![PyPI version](https://badge.fury.io/py/reliapi-sdk.svg)](https://pypi.org/project/reliapi-sdk/)
[![Docker](https://img.shields.io/docker/v/kikudoc/reliapi?label=docker)](https://hub.docker.com/r/kikudoc/reliapi)

## Features

- **Retries with Backoff** - Automatic retries with exponential backoff
- **Circuit Breaker** - Prevent cascading failures
- **Caching** - TTL cache for GET requests and LLM responses
- **Idempotency** - Request coalescing with idempotency keys
- **Rate Limiting** - Built-in rate limiting per tier
- **LLM Proxy** - Unified interface for OpenAI, Anthropic, Mistral
- **Cost Control** - Budget caps and cost estimation
- **Self-Service Onboarding** - Automated API key generation
- **Paddle Payments** - Subscription management

## Project Structure

```
reliapi/
├── core/                 # Core reliability components
│   ├── cache.py          # Redis-based TTL cache
│   ├── circuit_breaker.py
│   ├── idempotency.py    # Request coalescing
│   ├── retry.py          # Exponential backoff
│   ├── rate_limiter.py   # Per-tenant rate limits
│   ├── rate_scheduler.py # Token bucket algorithm
│   ├── key_pool.py       # Multi-key management
│   └── cost_estimator.py # LLM cost calculation
├── app/
│   ├── main.py           # FastAPI application
│   ├── services.py       # Business logic
│   ├── schemas.py        # Pydantic models
│   └── routes/           # Business routes
│       ├── paddle.py     # Payment processing
│       ├── onboarding.py # Self-service signup
│       ├── analytics.py  # Usage analytics
│       ├── calculators.py# ROI/pricing calculators
│       └── dashboard.py  # Admin dashboard
├── adapters/
│   └── llm/              # LLM provider adapters
│       ├── openai.py
│       ├── anthropic.py
│       └── mistral.py
├── config/               # Configuration loader
├── metrics/              # Prometheus metrics
├── examples/             # Code examples
├── integrations/         # LangChain, LlamaIndex
├── openapi/              # OpenAPI specs
├── postman/              # Postman collection
└── tests/                # Test suite
```

## Quick Start

### Using RapidAPI (No Installation)

Try ReliAPI directly on [RapidAPI](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi).

### Self-Hosting with Docker

```bash
docker run -d -p 8000:8000 \
  -e REDIS_URL="redis://localhost:6379/0" \
  -e RELIAPI_CONFIG_PATH=/app/config.yaml \
  kikudoc/reliapi:latest
```

### Local Development

```bash
# Clone repository
git clone https://github.com/kiku-jw/reliapi.git
cd reliapi

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Run server
export REDIS_URL=redis://localhost:6379/0
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Configuration

Create `config.yaml`:

```yaml
targets:
  openai:
    base_url: https://api.openai.com/v1
    llm:
      provider: openai
      default_model: gpt-4o-mini
      soft_cost_cap_usd: 0.10
      hard_cost_cap_usd: 0.50
    cache:
      enabled: true
      ttl_s: 3600
    circuit:
      error_threshold: 5
      cooldown_s: 60
    auth:
      type: bearer_env
      env_var: OPENAI_API_KEY
```

## API Endpoints

### Core Proxy

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/proxy/http` | POST | Proxy any HTTP API with reliability |
| `/proxy/llm` | POST | Proxy LLM requests with cost control |
| `/healthz` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

### Business Routes

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/paddle/plans` | GET | List subscription plans |
| `/paddle/checkout` | POST | Create checkout session |
| `/paddle/webhook` | POST | Handle Paddle webhooks |
| `/onboarding/start` | POST | Generate API key |
| `/onboarding/quick-start` | GET | Get quick start guide |
| `/onboarding/verify` | POST | Verify integration |
| `/calculators/pricing` | POST | Calculate pricing |
| `/calculators/roi` | POST | Calculate ROI |
| `/dashboard/metrics` | GET | Usage metrics |

## Environment Variables

```bash
# Required
REDIS_URL=redis://localhost:6379/0

# Optional
RELIAPI_CONFIG_PATH=config.yaml
RELIAPI_API_KEY=your-api-key
CORS_ORIGINS=*
LOG_LEVEL=INFO

# LLM Providers
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
MISTRAL_API_KEY=...

# Paddle (for payments)
PADDLE_API_KEY=...
PADDLE_VENDOR_ID=...
PADDLE_WEBHOOK_SECRET=...
PADDLE_ENVIRONMENT=sandbox
```

## SDK Usage

### Python

```python
from reliapi_sdk import ReliAPI

client = ReliAPI(
    base_url="https://reliapi.kikuai.dev",
    api_key="your-api-key"
)

# HTTP proxy
response = client.proxy_http(
    target="my-api",
    method="GET",
    path="/users/123",
    cache=300
)

# LLM proxy
llm_response = client.proxy_llm(
    target="openai",
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": "Hello!"}],
    idempotency_key="unique-key-123"
)
```

### JavaScript

```typescript
import { ReliAPI } from 'reliapi-sdk';

const client = new ReliAPI({
  baseUrl: 'https://reliapi.kikuai.dev',
  apiKey: 'your-api-key'
});

const response = await client.proxyLlm({
  target: 'openai',
  model: 'gpt-4o-mini',
  messages: [{ role: 'user', content: 'Hello!' }]
});
```

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=reliapi --cov-report=html
```

## Deployment

See [DEPLOYMENT.md](./docs/DEPLOYMENT.md) for production deployment guide.

## Documentation

- [OpenAPI Spec](./openapi/openapi.yaml)
- [Postman Collection](./postman/collection.json)
- [Live Docs](https://reliapi.kikuai.dev/docs)

## Support

- GitHub Issues: https://github.com/kiku-jw/reliapi/issues
- Email: dev@kikuai.dev

## License

AGPL-3.0. Copyright (c) 2025 KikuAI Lab
