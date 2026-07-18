# ReliAPI Code Examples

This directory contains code examples for using ReliAPI in different programming languages and frameworks.

## Quick Start

### Python

```bash
pip install httpx
export RAPIDAPI_KEY=your-key
python python_basic.py
```

### JavaScript/Node.js

```bash
npm install node-fetch  # or use built-in fetch in Node.js 18+
export RAPIDAPI_KEY=your-key
node javascript_basic.js
```

### TypeScript

```bash
npm install node-fetch @types/node-fetch
export RAPIDAPI_KEY=your-key
npx ts-node typescript_example.ts
```

### Go

```bash
go run go_example.go
```

### Rust

```bash
cargo run --example rust_example
```

## Examples Index

### Basic Examples

#### Python
- **`python_basic.py`** - Basic HTTP and LLM proxy usage with error handling and caching
- **`python_advanced.py`** - Advanced features and patterns
- **`error_handling.py`** - Comprehensive error handling examples

#### JavaScript/Node.js
- **`javascript_basic.js`** - Basic HTTP and LLM proxy usage with streaming support

#### TypeScript
- **`typescript_example.ts`** - TypeScript example with type safety and async/await

#### Go
- **`go_example.go`** - Go example with struct types and error handling

#### Rust
- **`rust_example.rs`** - Rust example with async/await and serde serialization

#### Shell
- **`curl_examples.sh`** - cURL examples for quick testing

### Integration Examples

See `integrations/` directory for framework-specific examples:
- **LangChain** (`integrations/langchain_example.py`) - Integration with LangChain
- **LlamaIndex** (`integrations/llamaindex_example.py`) - Integration with LlamaIndex
- **OpenAI SDK** (`integrations/openai_sdk_example.py`) - Drop-in replacement for OpenAI SDK

## Common Patterns

### 1. Basic LLM Request

**Python:**
```python
import httpx

response = httpx.post(
    "https://reliapi.kikuai.dev/proxy/llm",
    headers={"X-RapidAPI-Key": "your-key"},
    json={
        "target": "openai",
        "messages": [{"role": "user", "content": "Hello!"}],
        "model": "gpt-4o-mini"
    }
)
```

**JavaScript:**
```javascript
const response = await fetch('https://reliapi.kikuai.dev/proxy/llm', {
  method: 'POST',
  headers: {
    'X-RapidAPI-Key': 'your-key',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    target: 'openai',
    messages: [{ role: 'user', content: 'Hello!' }],
    model: 'gpt-4o-mini'
  })
});
```

### 2. Using Caching

Add `cache` parameter (in seconds) to enable caching:

```python
json={
    "target": "openai",
    "messages": [{"role": "user", "content": "What is Python?"}],
    "model": "gpt-4o-mini",
    "cache": 3600  # Cache for 1 hour
}
```

### 3. Using Idempotency

Add `idempotency_key` to prevent duplicate charges:

```python
import uuid

json={
    "target": "openai",
    "messages": [{"role": "user", "content": "Hello!"}],
    "model": "gpt-4o-mini",
    "idempotency_key": f"request-{uuid.uuid4()}"
}
```

### 4. Streaming Responses

Set `stream: true` and handle Server-Sent Events:

```python
response = httpx.post(
    "https://reliapi.kikuai.dev/proxy/llm",
    headers={
        "X-RapidAPI-Key": "your-key",
        "Accept": "text/event-stream"
    },
    json={
        "target": "openai",
        "messages": [{"role": "user", "content": "Count to 10"}],
        "model": "gpt-4o-mini",
        "stream": True
    },
    stream=True
)

for line in response.iter_lines():
    if line.startswith("data: "):
        data = json.loads(line[6:])
        print(data.get("choices", [{}])[0].get("delta", {}).get("content", ""), end="")
```

### 5. Error Handling

```python
try:
    response = httpx.post(...)
    if response.status_code == 200:
        data = response.json()
    else:
        error = response.json().get("error", {})
        if error.get("code") == "RATE_LIMIT_RELIAPI":
            retry_after = error.get("retry_after_s", 1.0)
            # Wait and retry
except httpx.TimeoutException:
    # Handle timeout
except httpx.RequestError as e:
    # Handle network error
```

## Configuration

### Environment Variables

- `RELIAPI_URL` - ReliAPI base URL (default: `https://reliapi.kikuai.dev`)
- `RAPIDAPI_KEY` - RapidAPI key (for RapidAPI usage)
- `RELIAPI_API_KEY` - ReliAPI API key (for self-hosted usage)

### Headers

**For RapidAPI:**
```python
headers = {"X-RapidAPI-Key": "your-rapidapi-key"}
```

**For Self-Hosted:**
```python
headers = {"Authorization": "Bearer your-reliapi-key"}
```

## Features Demonstrated

### All Examples Include:

1. **Basic HTTP Proxy** - Proxying any HTTP API through ReliAPI
2. **Basic LLM Proxy** - Making LLM API calls with reliability features
3. **Caching** - Demonstrating cost savings through caching
4. **Idempotency** - Preventing duplicate charges
5. **Error Handling** - Handling errors gracefully

### Language-Specific Features:

- **Python**: Uses `httpx` for async HTTP requests
- **JavaScript**: Uses `fetch` API with async/await
- **TypeScript**: Full type safety with interfaces
- **Go**: Struct types and error handling
- **Rust**: Async/await with `tokio` and `serde` serialization

## Response Structure

All examples demonstrate parsing ReliAPI responses:

```json
{
  "data": {
    "choices": [{
      "message": {
        "content": "Response text..."
      }
    }]
  },
  "meta": {
    "request_id": "req-123",
    "cache_hit": false,
    "idempotent_hit": false,
    "cost_usd": 0.000123,
    "duration_ms": 245
  }
}
```

## Benefits Shown

Each example demonstrates:

- ✅ **Automatic Retries** - ReliAPI handles retries automatically
- ✅ **Caching** - Reduce costs by 50-80% on repeated requests
- ✅ **Idempotency** - Prevent duplicate charges
- ✅ **Budget Caps** - Set limits to prevent surprise bills
- ✅ **Circuit Breaker** - Prevent cascading failures
- ✅ **Cost Tracking** - See exact cost per request

## Troubleshooting

### Connection Errors

- Verify `RELIAPI_URL` is correct
- Check network connectivity
- For self-hosted, ensure ReliAPI is running

### Authentication Errors

- Verify API key is set correctly
- Check header name (`X-RapidAPI-Key` vs `Authorization`)
- Ensure API key has proper permissions

### Timeout Errors

- Increase timeout value
- Check ReliAPI service status
- Verify upstream API (OpenAI, etc.) is accessible

## Additional Resources

- [ReliAPI Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)
- [RapidAPI ReliAPI Page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)
- [Integration Examples](../examples/integrations/README.md)
- [Postman Collection](../postman/collection.json)

## Contributing

Found a bug or want to add an example for another language? Please open an issue or submit a PR!














