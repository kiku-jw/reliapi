# ReliAPI Integration Examples

This directory contains examples showing how to integrate ReliAPI with popular LLM frameworks and SDKs.

## Examples

### 1. LangChain Integration (`langchain_example.py`)

Shows how to use ReliAPI with LangChain for building LLM applications.

**Features demonstrated:**
- Basic chat with ReliAPI
- Caching (reduce costs by 50-80%)
- Idempotency protection
- Streaming responses
- LangChain chains

**Installation:**
```bash
pip install langchain-openai
```

**Usage:**
```bash
export RAPIDAPI_KEY=your-rapidapi-key
python langchain_example.py
```

### 2. LlamaIndex Integration (`llamaindex_example.py`)

Shows how to use ReliAPI with LlamaIndex for RAG (Retrieval-Augmented Generation) applications.

**Features demonstrated:**
- Basic queries with ReliAPI
- Caching for cost reduction
- RAG pipeline integration
- Streaming responses
- Idempotency protection

**Installation:**
```bash
pip install llama-index-openai
```

**Usage:**
```bash
export RAPIDAPI_KEY=your-rapidapi-key
python llamaindex_example.py
```

### 3. OpenAI SDK Replacement (`openai_sdk_example.py`)

Shows how to replace direct OpenAI SDK calls with ReliAPI - just change the base URL!

**Features demonstrated:**
- Drop-in replacement (minimal code changes)
- Caching benefits
- Idempotency protection
- Streaming support
- Migration guide

**Installation:**
```bash
pip install openai
```

**Usage:**
```bash
export RAPIDAPI_KEY=your-rapidapi-key
export OPENAI_API_KEY=your-openai-key
python openai_sdk_example.py
```

## Quick Start

### Using RapidAPI

1. Get your RapidAPI key from [RapidAPI ReliAPI page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)
2. Set environment variable:
   ```bash
   export RAPIDAPI_KEY=your-rapidapi-key
   ```
3. Run any example:
   ```bash
   python langchain_example.py
   ```

### Using Self-Hosted ReliAPI

1. Set environment variables:
   ```bash
   export RELIAPI_API_KEY=your-reliapi-key
   export RELIAPI_BASE_URL=http://localhost:8000
   ```
2. Update the `RELIAPI_BASE_URL` in the example file
3. Run the example

## Key Benefits

All examples demonstrate these ReliAPI benefits:

- **Automatic Retries**: Failed requests automatically retry with exponential backoff
- **Caching**: Reduce costs by 50-80% on repeated requests
- **Idempotency**: Prevent duplicate charges when users click twice or retries happen
- **Budget Caps**: Set hard limits to prevent surprise bills
- **Circuit Breaker**: Automatically stops calling failing services
- **Cost Tracking**: See exactly what each LLM call costs in real-time

## Common Patterns

### Adding Idempotency

Add `X-Idempotency-Key` header to prevent duplicate charges:

```python
headers = {
    "X-RapidAPI-Key": RAPIDAPI_KEY,
    "X-Idempotency-Key": "unique-key-per-request"
}
```

### Enabling Caching

ReliAPI automatically caches responses. Cache duration is configured server-side, but you can hint cache behavior via headers.

### Streaming Support

All examples support streaming. Just set `stream=True` in your LLM calls - ReliAPI handles it transparently.

## Troubleshooting

### 401 Unauthorized
- Check that your API key is set correctly
- For RapidAPI, ensure `X-RapidAPI-Key` header is present
- For self-hosted, ensure `Authorization: Bearer <token>` header is present

### Connection Errors
- Verify `baseUrl` is correct for your environment
- Check network connectivity
- For self-hosted, ensure ReliAPI is running

### Import Errors
- Install required dependencies: `pip install langchain-openai llama-index-openai openai`
- Check Python version (3.8+ required)

## Additional Resources

- [ReliAPI Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)
- [RapidAPI ReliAPI Page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)
- [LangChain Documentation](https://python.langchain.com/)
- [LlamaIndex Documentation](https://docs.llamaindex.ai/)
- [OpenAI SDK Documentation](https://platform.openai.com/docs/api-reference)














