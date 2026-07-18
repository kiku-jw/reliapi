# ReliAPI Postman Collection

This directory contains Postman collections and environments for testing ReliAPI.

## Files

- `collection.json` - Main Postman collection with all ReliAPI endpoints
- `environments/rapidapi.postman_environment.json` - Environment for RapidAPI usage
- `environments/self-hosted.postman_environment.json` - Environment for self-hosted ReliAPI

## Quick Start

### 1. Import Collection and Environment

1. Open Postman
2. Click **Import** button
3. Import `collection.json`
4. Import one of the environment files from `environments/` directory

### 2. Configure Environment Variables

#### For RapidAPI:
- Set `rapidApiKey` to your RapidAPI key
- Set `baseUrl` to `https://reliapi.kikuai.dev` (default)
- Optionally set provider API keys (`openaiApiKey`, `anthropicApiKey`, `mistralApiKey`)

#### For Self-Hosted:
- Set `apiKey` to your ReliAPI API key
- Set `baseUrl` to your ReliAPI instance URL (default: `http://localhost:8000`)
- Optionally set provider API keys

### 3. Select Environment

In Postman, select the appropriate environment from the dropdown in the top-right corner.

## Collection Features

### Pre-request Scripts

The collection includes automatic pre-request scripts that:
- Automatically add `X-RapidAPI-Key` header when using RapidAPI environment
- Automatically add `Authorization` header when using self-hosted environment
- No manual header configuration needed!

### Tests

Each endpoint includes automated tests that verify:
- Status codes
- Response structure
- Response times
- Required fields

### Examples Included

#### LLM Proxy Examples:
- **OpenAI GPT-4o-mini** - Basic LLM request with caching and idempotency
- **Anthropic Claude 3 Sonnet** - Example with Anthropic provider
- **Mistral Large** - Example with Mistral provider
- **Streaming (SSE)** - Server-Sent Events streaming example

#### HTTP Proxy Examples:
- **JSONPlaceholder API** - Example of proxying a GET request with caching

#### Health Check Examples:
- **Healthz** - Health check endpoint
- **Readyz** - Readiness check endpoint
- **Livez** - Liveness check endpoint
- **Metrics** - Prometheus metrics endpoint

## Usage Tips

1. **Idempotency Keys**: All LLM examples use `{{$randomUUID}}` to generate unique idempotency keys. This ensures each request is unique while still benefiting from idempotency protection.

2. **Caching**: Set `cache` parameter (in seconds) to enable caching. Examples use 3600 seconds (1 hour) for LLM requests and 300 seconds (5 minutes) for HTTP requests.

3. **Streaming**: For streaming responses, set `stream: true` and `Accept: text/event-stream` header. See the "Proxy LLM request - Streaming (SSE)" example.

4. **Testing**: Run the entire collection using Postman's Collection Runner to test all endpoints at once.

## Environment Variables Reference

### RapidAPI Environment
- `baseUrl` - ReliAPI base URL (default: `https://reliapi.kikuai.dev`)
- `rapidApiKey` - Your RapidAPI subscription key
- `openaiApiKey` - OpenAI API key (optional, if using OpenAI provider)
- `anthropicApiKey` - Anthropic API key (optional, if using Anthropic provider)
- `mistralApiKey` - Mistral API key (optional, if using Mistral provider)

### Self-Hosted Environment
- `baseUrl` - Your ReliAPI instance URL (default: `http://localhost:8000`)
- `apiKey` - Your ReliAPI API key
- `openaiApiKey` - OpenAI API key (optional)
- `anthropicApiKey` - Anthropic API key (optional)
- `mistralApiKey` - Mistral API key (optional)

## Troubleshooting

### 401 Unauthorized
- Check that your API key is set correctly in the environment
- For RapidAPI, ensure `X-RapidAPI-Key` header is present
- For self-hosted, ensure `Authorization: Bearer <token>` header is present

### 422 Validation Error
- Check request body format matches the examples
- Ensure required fields are present (`target`, `messages` for LLM, etc.)

### Connection Errors
- Verify `baseUrl` is correct for your environment
- Check network connectivity
- For self-hosted, ensure ReliAPI is running

## Additional Resources

- [ReliAPI Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)
- [RapidAPI ReliAPI Page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)
- [OpenAPI Specification](../openapi/openapi.yaml)














