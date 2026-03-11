# ReliAPI Documentation

## Overview

ReliAPI is a **reliability layer** for HTTP and LLM APIs. It adds automatic retries, circuit breakers, caching, idempotency, and cost controls to any API call.

**Key Features:**

- 🔄 **Automatic Retries** - Smart retry logic with exponential backoff
- ⚡ **Circuit Breaker** - Automatic failure detection and cooldown
- 💾 **Caching** - TTL-based cache to reduce API calls
- 🔑 **Idempotency** - Request coalescing prevents duplicate charges
- 💰 **Budget Caps** - Hard and soft cost limits for LLM APIs
- 📊 **Observability** - Request IDs, metrics, and structured errors

**Use Cases:**

- Make LLM API calls more reliable and cost-predictable
- Add retry logic to any HTTP API
- Prevent duplicate requests with idempotency
- Cache responses to reduce API costs
- Monitor API usage with Prometheus metrics

## Where to Get ReliAPI

ReliAPI is available in multiple formats:

- **RapidAPI**: [Try ReliAPI on RapidAPI](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi) - No installation required, use directly from RapidAPI
- **NPM Package**: [reliapi-sdk](https://www.npmjs.com/package/reliapi-sdk) - `npm install reliapi-sdk`
- **PyPI Package**: [reliapi-sdk](https://pypi.org/project/reliapi-sdk/) - `pip install reliapi-sdk`
- **Docker Image**: [kikudoc/reliapi](https://hub.docker.com/r/kikudoc/reliapi) - `docker pull kikudoc/reliapi`
- **CLI Package**: [reliapi-cli](https://pypi.org/project/reliapi-cli/) - `pip install reliapi-cli`
- **GitHub Repository**: [kiku-jw/reliapi](https://github.com/kiku-jw/reliapi) - Source code and documentation

---

## Base URL

```
https://reliapi.kikuai.dev
```

---

## Authentication

All requests require the `X-RapidAPI-Key` header (automatically added by RapidAPI):

```http
X-RapidAPI-Key: your-rapidapi-key
```

---

## Endpoints

### Health Check

#### `GET /healthz`

Check if the API is healthy and ready to accept requests.

**Response:**

```json
{
  "status": "healthy"
}
```

**Use Cases:**

- Load balancer health checks
- Monitoring and alerting

---

### LLM Proxy

#### `POST /proxy/llm`

Proxy requests to LLM providers (OpenAI, Anthropic, Mistral) with reliability features.

**Request Body:**

```json
{
  "target": "openai",
  "messages": [
    {
      "role": "user",
      "content": "Hello, world!"
    }
  ],
  "model": "gpt-4o-mini",
  "max_tokens": 100,
  "temperature": 0.7,
  "stream": false
}
```

**Request Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `target` | string | Yes | LLM provider name: `openai`, `anthropic`, `mistral` |
| `messages` | array | Yes | Array of message objects with `role` and `content` |
| `model` | string | No | Model name (defaults to provider's default) |
| `max_tokens` | integer | No | Maximum tokens in response |
| `temperature` | float | No | Sampling temperature (0.0-2.0) |
| `top_p` | float | No | Top-p sampling parameter |
| `stream` | boolean | No | Enable streaming (SSE format) |
| `idempotency_key` | string | No | Key for request deduplication |

**Success Response (200):**

```json
{
  "success": true,
  "data": {
    "content": "Hello! How can I help you today?",
    "role": "assistant",
    "finish_reason": "stop",
    "usage": {
      "prompt_tokens": 10,
      "completion_tokens": 8,
      "total_tokens": 18
    }
  },
  "meta": {
    "target": "openai",
    "provider": "openai",
    "model": "gpt-4o-mini",
    "cache_hit": false,
    "idempotent_hit": false,
    "retries": 0,
    "duration_ms": 1250,
    "request_id": "req_1234567890_abc12345",
    "cost_usd": 0.000012,
    "cost_estimate_usd": 0.000015
  }
}
```

**Error Response (4xx/5xx):**

```json
{
  "success": false,
  "error": {
    "type": "budget_error",
    "code": "BUDGET_EXCEEDED",
    "message": "Estimated cost $0.05 exceeds hard cap $0.01",
    "retryable": false,
    "target": "openai",
    "status_code": 400
  },
  "meta": {
    "target": "openai",
    "retries": 0,
    "duration_ms": 5,
    "request_id": "req_1234567890_abc12345"
  }
}
```

**Response Headers:**

- `X-Request-ID` - Unique request identifier
- `X-Cache-Hit` - Whether response was from cache (`true`/`false`)
- `X-Retries` - Number of retries performed
- `X-Duration-MS` - Request duration in milliseconds

---

### HTTP Proxy

#### `POST /proxy/http`

Proxy requests to any HTTP API with reliability features.

**Request Body:**

```json
{
  "target": "my_api",
  "method": "GET",
  "path": "/users/123",
  "headers": {
    "Custom-Header": "value"
  },
  "query": {
    "include": "profile"
  },
  "body": null,
  "idempotency_key": "req-123"
}
```

**Request Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `target` | string | Yes | Target API name from configuration |
| `method` | string | Yes | HTTP method: `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD`, `OPTIONS` |
| `path` | string | Yes | API path (e.g., `/users/123`) |
| `headers` | object | No | Custom HTTP headers |
| `query` | object | No | Query parameters |
| `body` | string | No | Request body (JSON string for POST/PUT/PATCH) |
| `idempotency_key` | string | No | Key for request deduplication |
| `cache` | integer | No | Cache TTL in seconds (only for GET/HEAD) |

**Success Response (200):**

```json
{
  "success": true,
  "data": {
    "status_code": 200,
    "headers": {
      "Content-Type": "application/json"
    },
    "body": {
      "id": 123,
      "name": "John"
    }
  },
  "meta": {
    "target": "my_api",
    "cache_hit": false,
    "idempotent_hit": false,
    "retries": 0,
    "duration_ms": 145,
    "request_id": "req_1234567890_abc12345"
  }
}
```

---

## Code Examples

### Python

```python
import requests

# LLM Proxy Example
response = requests.post(
    "https://reliapi.kikuai.dev/proxy/llm",
    headers={
        "X-RapidAPI-Key": "your-rapidapi-key",
        "Content-Type": "application/json"
    },
    json={
        "target": "openai",
        "messages": [
            {"role": "user", "content": "Say 'Hello, ReliAPI!' only."}
        ],
        "model": "gpt-4o-mini",
        "max_tokens": 20
    }
)

if response.status_code == 200:
    data = response.json()
    print("Content:", data["data"]["content"])
    print("Cost: $", data["meta"]["cost_usd"])
else:
    error = response.json()
    print("Error:", error["error"]["message"])
```

### JavaScript

```javascript
// LLM Proxy Example
const response = await fetch('https://reliapi.kikuai.dev/proxy/llm', {
  method: 'POST',
  headers: {
    'X-RapidAPI-Key': 'your-rapidapi-key',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    target: 'openai',
    messages: [
      { role: 'user', content: "Say 'Hello, ReliAPI!' only." }
    ],
    model: 'gpt-4o-mini',
    max_tokens: 20
  })
});

const data = await response.json();
if (data.success) {
  console.log('Content:', data.data.content);
  console.log('Cost: $', data.meta.cost_usd);
} else {
  console.error('Error:', data.error.message);
}
```

### cURL

```bash
curl -X POST "https://reliapi.kikuai.dev/proxy/llm" \
  -H "X-RapidAPI-Key: your-rapidapi-key" \
  -H "Content-Type: application/json" \
  -d '{
    "target": "openai",
    "messages": [
      {"role": "user", "content": "Say hello"}
    ],
    "model": "gpt-4o-mini"
  }'
```

---

## Error Handling

All errors follow a consistent format:

```json
{
  "success": false,
  "error": {
    "type": "error_type",
    "code": "ERROR_CODE",
    "message": "Human-readable error message",
    "retryable": true,
    "target": "openai",
    "status_code": 429
  },
  "meta": {
    "target": "openai",
    "retries": 2,
    "duration_ms": 1250,
    "request_id": "req_1234567890_abc12345"
  }
}
```

**Error Types:**

- `client_error` - Invalid request (400, 401, 403, 404)
- `upstream_error` - Upstream API error (500, 502, 503)
- `rate_limit_error` - Rate limit exceeded (429)
- `budget_error` - Budget cap exceeded
- `circuit_breaker_error` - Circuit breaker is open
- `timeout_error` - Request timeout

**Retryable Errors:**

- `429` (Rate Limit) - Retry after `retry_after_s` seconds
- `5xx` (Server Errors) - Retry with exponential backoff
- Network errors - Retry with exponential backoff

---

## Idempotency

Use the `idempotency_key` parameter to prevent duplicate requests:

```json
{
  "target": "openai",
  "messages": [{"role": "user", "content": "Hello"}],
  "idempotency_key": "unique-request-id-123"
}
```

**Benefits:**

- If the same request is made twice with the same key, only one API call is executed
- The second request returns the cached response
- Prevents duplicate charges for LLM APIs

---

## Caching

Responses are automatically cached based on request content:

- **LLM requests**: Cached by messages + model + parameters
- **HTTP GET/HEAD**: Cached by URL + headers + query params

**Cache Control:**

- Default TTL: Configured per target
- Override TTL: Use `cache` parameter (seconds)
- Cache hit indicator: `meta.cache_hit` in response

---

## Streaming (SSE)

Enable streaming for LLM responses:

```json
{
  "target": "openai",
  "messages": [{"role": "user", "content": "Count from 1 to 5"}],
  "stream": true
}
```

**Response Format:** Server-Sent Events (SSE)

```
data: {"choices":[{"delta":{"content":"1"}}]}

data: {"choices":[{"delta":{"content":" 2"}}]}

data: [DONE]
```

**Note:** Streaming is currently supported for OpenAI only.

---

## Rate Limits

ReliAPI implements rate limiting to protect upstream APIs:

- **Free Tier**: 500,000 requests/month
- **Rate Limit Headers**: `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- **Rate Limit Errors**: `429` status with `retry_after_s` in error response

---

## Best Practices

1. **Always use idempotency keys** for critical operations
2. **Handle retryable errors** - Check `error.retryable` and retry if true
3. **Monitor costs** - Check `meta.cost_usd` for LLM requests
4. **Use caching** - Set appropriate cache TTL for repeated requests
5. **Check cache hits** - Use `meta.cache_hit` to optimize cache usage

---

## Support

- **Documentation**: [GitHub Repository](https://github.com/kiku-jw/reliapi)
- **Live Demo**: [Interactive Demo](https://kikuai-lab.github.io/reliapi/)
- **Issues**: [GitHub Issues](https://github.com/kiku-jw/reliapi/issues)
- **NPM Package**: [reliapi-sdk](https://www.npmjs.com/package/reliapi-sdk)
- **PyPI Package**: [reliapi-sdk](https://pypi.org/project/reliapi-sdk/)
- **Docker Image**: [kikudoc/reliapi](https://hub.docker.com/r/kikudoc/reliapi)
- **RapidAPI**: [Try ReliAPI on RapidAPI](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)

---

## License

AGPL-3.0-only - See [LICENSE](https://github.com/kiku-jw/reliapi/blob/main/LICENSE) for details.
