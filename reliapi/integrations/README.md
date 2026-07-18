# ReliAPI Framework Integrations

This directory contains integrations for popular LLM frameworks and tools.

## Available Integrations

### LangChain

**Location:** `langchain/`

Drop-in replacement for LangChain's ChatOpenAI that routes through ReliAPI.

**Features:**
- Automatic caching
- Idempotency protection
- Automatic retries
- Cost tracking

**Quick Start:**
```python
from reliapi.integrations.langchain import ReliAPIChatOpenAI

llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)
```

**Documentation:** [langchain/README.md](langchain/README.md)

### LlamaIndex

**Location:** `llamaindex/`

Drop-in replacement for LlamaIndex's OpenAI LLM that routes through ReliAPI.

**Features:**
- Works with RAG pipelines
- Automatic caching
- Idempotency protection
- Streaming support

**Quick Start:**
```python
from reliapi.integrations.llamaindex import ReliAPIOpenAI

llm = ReliAPIOpenAI(
    api_base="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)
```

**Documentation:** [llamaindex/README.md](llamaindex/README.md)

### Flowise

**Location:** `flowise/`

Guide for integrating ReliAPI with Flowise low-code LLM tool.

**Features:**
- Custom node configuration
- Environment variable setup
- Proxy middleware

**Documentation:** [flowise/README.md](flowise/README.md)

## Common Benefits

All integrations provide:

- ✅ **Cost Savings:** 50-80% reduction through caching
- ✅ **Reliability:** Automatic retries and circuit breaker
- ✅ **No Duplicate Charges:** Idempotency protection
- ✅ **Budget Control:** Set hard limits to prevent surprises
- ✅ **Cost Visibility:** Track spending in real-time

## Installation

### LangChain

```bash
pip install langchain-openai reliapi
```

### LlamaIndex

```bash
pip install llama-index-openai reliapi
```

### Flowise

Follow the guide in [flowise/README.md](flowise/README.md)

## Examples

See [examples/integrations/](../../examples/integrations/) for complete examples:
- `langchain_example.py` - LangChain integration examples
- `llamaindex_example.py` - LlamaIndex integration examples
- `openai_sdk_example.py` - OpenAI SDK replacement examples

## Configuration

### Using RapidAPI

All integrations support RapidAPI:

```python
# Set rapidapi_key parameter
rapidapi_key="your-rapidapi-key"
```

### Using Self-Hosted ReliAPI

All integrations support self-hosted ReliAPI:

```python
# Set reliapi_key parameter and custom base_url
reliapi_key="your-reliapi-key"
base_url="http://localhost:8000/proxy/llm"
```

## Migration Guide

### From Direct API Calls

1. **Install integration:**
   ```bash
   pip install reliapi
   ```

2. **Import integration:**
   ```python
   from reliapi.integrations.langchain import ReliAPIChatOpenAI
   ```

3. **Replace LLM initialization:**
   ```python
   # Before
   llm = ChatOpenAI(model="gpt-4o-mini")
   
   # After
   llm = ReliAPIChatOpenAI(
       base_url="https://reliapi.kikuai.dev/proxy/llm",
       model="gpt-4o-mini",
       rapidapi_key="your-key"
   )
   ```

4. **That's it!** Your existing code works without changes.

## Support

- [GitHub Issues](https://github.com/KikuAI-Lab/reliapi/issues)
- [Email](mailto:dev@kikuai.dev)
- [Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)














