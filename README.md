# ReliAPI

Beta self-hosted proxy for HTTP and LLM calls with Redis-backed caching,
non-streaming LLM idempotency, and configurable LLM budget guardrails.

[![npm version](https://badge.fury.io/js/reliapi-sdk.svg)](https://www.npmjs.com/package/reliapi-sdk)
[![PyPI version](https://badge.fury.io/py/reliapi-sdk.svg)](https://pypi.org/project/reliapi-sdk/)
[![Docker](https://img.shields.io/docker/v/kikudoc/reliapi?label=docker)](https://hub.docker.com/r/kikudoc/reliapi)

**[Open the project site](https://kikuai-lab.github.io/reliapi/)**

> [!IMPORTANT]
> ReliAPI is beta software for evaluation and self-hosting. It does not provide
> an SLA, managed multi-upstream fallback, exactly-once delivery, quantified
> savings, or published performance targets.

## Features

- **Caching** - TTL cache for HTTP GET/HEAD and non-streaming LLM responses
- **Idempotency** - Redis-backed primitives used by the non-streaming LLM path
- **LLM Proxy** - Configured OpenAI, Anthropic, and Mistral targets
- **Budget Guardrails** - Pre-request estimates with hard and soft caps
- **Rate Limiting** - Built-in request limits per configured tier
- **Experimental Resilience** - Retry and circuit-breaker code remains under
  beta review and is not part of the documented reliability baseline

## Project Structure

```
reliapi/
├── reliapi/              # Importable Python package
│   ├── app/              # FastAPI application and routes
│   ├── core/             # Reliability primitives
│   ├── adapters/         # Provider adapters
│   ├── config/           # Configuration loader and schema
│   ├── integrations/     # RapidAPI, RouteLLM, framework adapters
│   └── metrics/          # Prometheus metrics
├── cli/                  # CLI package
├── action/               # GitHub Action
├── scripts/              # OpenAPI / SDK / release helpers
├── sdk/                  # SDK generation templates
├── examples/             # Code examples
├── openapi/              # OpenAPI specs
├── postman/              # Postman collection
└── tests/                # Test suite
```

## Quick Start

### Using RapidAPI (No Installation)

Try ReliAPI directly on [RapidAPI](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi).

### Self-Hosting with Docker

```bash
git clone https://github.com/KikuAI-Lab/reliapi.git
cd reliapi
cp .env.example .env

# Add the provider key(s) referenced by config.yaml to .env, then start
# ReliAPI and its Redis service.
docker compose up -d --build
curl http://localhost:8000/healthz
```

### Local Development

```bash
# Clone repository
git clone https://github.com/KikuAI-Lab/reliapi.git
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
export RELIAPI_CONFIG_PATH=config.yaml
uvicorn reliapi.app.main:app --host 0.0.0.0 --port 8000 --reload
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
| `/v1/proxy/http` | POST | Proxy a request to a configured HTTP target |
| `/v1/proxy/llm` | POST | Proxy a request to a configured LLM target |
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
    base_url="http://localhost:8000",
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
  baseUrl: 'http://localhost:8000',
  apiKey: 'your-api-key'
});

const response = await client.proxyLlm({
  target: 'openai',
  model: 'gpt-4o-mini',
  messages: [{ role: 'user', content: 'Hello!' }]
});
```

## Beta Limitations

- Redis must be reachable for shared cache and idempotency behavior.
- HTTP response caching applies to GET and HEAD; LLM caching applies to the
  non-streaming path.
- The documented idempotency baseline covers non-streaming LLM calls. HTTP
  idempotency and streaming behavior remain under beta review.
- Idempotency is not a promise of exactly-once upstream execution or provider
  billing behavior.
- Budget caps use pre-request estimates, not authoritative invoices or
  account-wide spending limits.
- Deployment security, target allow-lists, Redis durability, observability, and
  provider quotas remain the self-hosting operator's responsibility.

## Testing

```bash
# Run tests
pytest

# With coverage
pytest --cov=reliapi --cov-report=html
```

## Release Tooling

- `make openapi` regenerates the OpenAPI schema from the FastAPI app
- `make postman` rebuilds the Postman collection
- `make sdk-js` and `make sdk-py` regenerate SDK packages
- `make release-patch|minor|major` bumps version metadata and prepares a tagged release
- `make cli` installs the local CLI package for smoke testing

See [`docs/release.md`](./docs/release.md) and [`docs/SECRETS_SETUP.md`](./docs/SECRETS_SETUP.md) for release ops.

## Documentation

- [OpenAPI Spec](./openapi/openapi.yaml)
- [Postman Collection](./postman/collection.json)
- [Project Site](https://kikuai-lab.github.io/reliapi/)
- [Wiki](https://github.com/KikuAI-Lab/reliapi/wiki)

## Support

- GitHub Issues: https://github.com/KikuAI-Lab/reliapi/issues
- Email: dev@kikuai.dev

## Follow the work

Project notes and new tools: [Telegram](https://t.me/kiku_ai) ·
[LinkedIn](https://www.linkedin.com/in/kiku-jw/) ·
[KikuAI](https://kikuai.dev/)

## License

AGPL-3.0. Copyright (c) 2025 KikuAI Lab
