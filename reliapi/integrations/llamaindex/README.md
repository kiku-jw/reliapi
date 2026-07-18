# ReliAPI LlamaIndex Integration

Seamless integration between ReliAPI and LlamaIndex for reliable LLM API calls.

## Installation

```bash
pip install llama-index-openai reliapi
```

## Quick Start

### Basic Usage

```python
from reliapi.integrations.llamaindex import ReliAPIOpenAI
from llama_index.core import Settings

# Initialize with ReliAPI
llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-rapidapi-key"
)

# Set as default LLM
Settings.llm = llm

# Use like regular LlamaIndex LLM
response = llm.complete("What is Python?")
print(response.text)
```

### With RAG Pipeline

```python
from reliapi.integrations.llamaindex import ReliAPIOpenAI
from llama_index.core import VectorStoreIndex, Document, Settings

llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)

Settings.llm = llm

# Create documents
documents = [Document(text="ReliAPI is a reliability layer for LLM APIs.")]

# Create index
index = VectorStoreIndex.from_documents(documents)

# Query
query_engine = index.as_query_engine()
response = query_engine.query("What does ReliAPI provide?")
print(response)
```

### With Caching

```python
llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)

# First query - calls OpenAI API
response1 = llm.complete("What is idempotency?")

# Second query - served from cache (FREE!)
response2 = llm.complete("What is idempotency?")
```

## Features

- ✅ **Drop-in replacement** - Works with existing LlamaIndex code
- ✅ **Automatic caching** - Reduce costs by 50-80%
- ✅ **Idempotency** - Prevent duplicate charges
- ✅ **Automatic retries** - Handle failures gracefully
- ✅ **Cost tracking** - See exact cost per request
- ✅ **Budget caps** - Prevent surprise bills

## Configuration

### Using RapidAPI

```python
llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    rapidapi_key="your-rapidapi-key",
    model="gpt-4o-mini"
)
```

### Using Self-Hosted ReliAPI

```python
llm = ReliAPIOpenAI(
    api_base="http://localhost:8000/proxy/llm",
    reliapi_key="your-reliapi-key",
    model="gpt-4o-mini"
)
```

## Advanced Usage

### Custom Headers

```python
llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    rapidapi_key="your-key",
    additional_kwargs={
        "headers": {
            "X-Idempotency-Key": "custom-key-123",
            "X-Cache-TTL": "7200"  # 2 hours
        }
    }
)
```

### Streaming

```python
llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    rapidapi_key="your-key"
)

response_stream = llm.stream_complete("Write a haiku about reliability.")
for token in response_stream:
    print(token.delta, end="", flush=True)
```

## Migration Guide

### Before (Direct OpenAI)

```python
from llama_index.llms.openai import OpenAI

llm = OpenAI(
    model="gpt-4o-mini",
    api_key="sk-..."
)
```

### After (With ReliAPI)

```python
from reliapi.integrations.llamaindex import ReliAPIOpenAI

llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)
```

**That's it!** Your existing LlamaIndex code works without any other changes.

## Benefits

- **Cost Savings:** 50-80% reduction through caching
- **Reliability:** Automatic retries and circuit breaker
- **No Duplicate Charges:** Idempotency protection
- **Budget Control:** Set hard limits to prevent surprises
- **Cost Visibility:** Track spending in real-time

## Examples

See [examples/integrations/llamaindex_example.py](../../examples/integrations/llamaindex_example.py) for complete examples.

## Documentation

- [ReliAPI Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)
- [LlamaIndex Documentation](https://docs.llamaindex.ai/)
- [RapidAPI ReliAPI Page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)

## Support

- [GitHub Issues](https://github.com/KikuAI-Lab/reliapi/issues)
- [Email](mailto:dev@kikuai.dev)














