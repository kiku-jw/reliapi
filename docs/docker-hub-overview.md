# ReliAPI - Reliability Layer for HTTP APIs and LLM Calls

**ReliAPI** is a production-ready reliability layer that adds retries, caching, idempotency, circuit breakers, and cost control to your HTTP APIs and LLM calls.

## 🚀 Quick Start

```bash
docker run -d -p 8000:8000 \
  -e REDIS_URL="redis://localhost:6379/0" \
  kikudoc/reliapi:latest
```

The API will be available at `http://localhost:8000`

## ✨ Key Features

- **🔄 Automatic Retries** - Exponential backoff with configurable retry policies
- **⚡ Circuit Breaker** - Prevent cascading failures with intelligent circuit breaking
- **💾 Smart Caching** - TTL-based caching for GET requests and LLM responses
- **🔑 Idempotency** - Request coalescing with idempotency keys to prevent duplicate operations
- **🚦 Rate Limiting** - Built-in rate limiting per tier and endpoint
- **🤖 LLM Proxy** - Unified interface for OpenAI, Anthropic, Mistral with cost control
- **💰 Cost Control** - Budget caps and real-time cost estimation for LLM calls
- **📊 Health Monitoring** - Built-in health check endpoint (`/healthz`)

## 📖 Usage Examples

### HTTP Proxy with Retries

```bash
curl -X POST http://localhost:8000/proxy/http \
  -H "Content-Type: application/json" \
  -d '{
    "target": "my-api",
    "method": "GET",
    "path": "/users/123",
    "cache": 300
  }'
```

### LLM Proxy with Idempotency

```bash
curl -X POST http://localhost:8000/proxy/llm \
  -H "Content-Type: application/json" \
  -d '{
    "target": "openai",
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "Hello!"}],
    "idempotency_key": "unique-key-123"
  }'
```

## 🔧 Configuration

### Environment Variables

- `REDIS_URL` - Redis connection string (required for caching and idempotency)
- `RELIAPI_STRICT_CONFIG` - Enable strict configuration validation (default: `false`)
- `LOG_LEVEL` - Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR` (default: `INFO`)

### Docker Compose Example

```yaml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
  
  reliapi:
    image: kikudoc/reliapi:latest
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
    depends_on:
      - redis
```

## 📚 Documentation

- **GitHub Repository**: [kiku-jw/reliapi](https://github.com/kiku-jw/reliapi)
- **NPM Package**: [reliapi-sdk](https://www.npmjs.com/package/reliapi-sdk)
- **PyPI Package**: [reliapi-sdk](https://pypi.org/project/reliapi-sdk/)
- **OpenAPI Spec**: Available in the repository

## 🏥 Health Check

```bash
curl http://localhost:8000/healthz
```

Returns `200 OK` when the service is healthy.

## 📦 Image Tags

- `latest` - Latest stable release
- `v1.0.x` - Specific version tags (e.g., `v1.0.7`)

## 🔒 Security

- No secrets stored in the image
- All sensitive configuration via environment variables
- Supports HTTPS/TLS termination at the reverse proxy level

## 📄 License

AGPL-3.0-only - see [LICENSE](https://github.com/kiku-jw/reliapi/blob/main/LICENSE) for details.

## 💬 Support

- **GitHub Issues**: [Report a bug or request a feature](https://github.com/kiku-jw/reliapi/issues)
- **Email**: dev@kikuai.dev

---

**Made with ❤️ by [KikuAI-Lab](https://github.com/KikuAI-Lab)**
