# ReliAPI LangChain Integration

Seamless integration between ReliAPI and LangChain for reliable LLM API calls.

## Installation

```bash
pip install langchain-openai reliapi
```

## Quick Start

### Basic Usage

```python
from reliapi.integrations.langchain import ReliAPIChatOpenAI

# Initialize with ReliAPI
llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-rapidapi-key"
)

# Use like regular LangChain LLM
response = llm.invoke("What is Python?")
print(response.content)
```

### With Caching

```python
llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key",
    # Caching is automatic, but you can configure TTL via headers
)

# First request - calls OpenAI API
response1 = llm.invoke("What is idempotency?")

# Second request - served from cache (FREE!)
response2 = llm.invoke("What is idempotency?")
```

### With LangChain Chains

```python
from langchain.chains import LLMChain
from langchain.prompts import ChatPromptTemplate
from reliapi.integrations.langchain import ReliAPIChatOpenAI

llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)

prompt = ChatPromptTemplate.from_template(
    "Translate the following {language} text to English: {text}"
)

chain = LLMChain(llm=llm, prompt=prompt)
result = chain.run(language="Spanish", text="Hola, ¿cómo estás?")
```

## Features

- ✅ **Drop-in replacement** - Works with existing LangChain code
- ✅ **Automatic caching** - Reduce costs by 50-80%
- ✅ **Idempotency** - Prevent duplicate charges
- ✅ **Automatic retries** - Handle failures gracefully
- ✅ **Cost tracking** - See exact cost per request
- ✅ **Budget caps** - Prevent surprise bills

## Configuration

### Using RapidAPI

```python
llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    rapidapi_key="your-rapidapi-key",
    model="gpt-4o-mini"
)
```

### Using Self-Hosted ReliAPI

```python
llm = ReliAPIChatOpenAI(
    base_url="http://localhost:8000/proxy/llm",
    reliapi_key="your-reliapi-key",
    model="gpt-4o-mini"
)
```

## Advanced Usage

### Custom Headers

```python
llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    rapidapi_key="your-key",
    default_headers={
        "X-Idempotency-Key": "custom-key-123",
        "X-Cache-TTL": "7200"  # 2 hours
    }
)
```

### Streaming

```python
llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    rapidapi_key="your-key",
    streaming=True
)

for chunk in llm.stream("Write a haiku about reliability."):
    print(chunk.content, end="", flush=True)
```

## Migration Guide

### Before (Direct OpenAI)

```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    model="gpt-4o-mini",
    openai_api_key="sk-..."
)
```

### After (With ReliAPI)

```python
from reliapi.integrations.langchain import ReliAPIChatOpenAI

llm = ReliAPIChatOpenAI(
    base_url="https://reliapi.kikuai.dev/proxy/llm",
    model="gpt-4o-mini",
    rapidapi_key="your-key"
)
```

**That's it!** Your existing LangChain code works without any other changes.

## Benefits

- **Cost Savings:** 50-80% reduction through caching
- **Reliability:** Automatic retries and circuit breaker
- **No Duplicate Charges:** Idempotency protection
- **Budget Control:** Set hard limits to prevent surprises
- **Cost Visibility:** Track spending in real-time

## Examples

See [examples/integrations/langchain_example.py](../../examples/integrations/langchain_example.py) for complete examples.

## Documentation

- [ReliAPI Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)
- [LangChain Documentation](https://python.langchain.com/)
- [RapidAPI ReliAPI Page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)

## Support

- [GitHub Issues](https://github.com/KikuAI-Lab/reliapi/issues)
- [Email](mailto:dev@kikuai.dev)














