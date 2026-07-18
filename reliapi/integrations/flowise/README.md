# ReliAPI Flowise Integration

Guide for integrating ReliAPI with Flowise for reliable LLM API calls.

## Overview

Flowise is a low-code tool for building LLM applications. This guide shows how to use ReliAPI with Flowise to add reliability features like caching, retries, and idempotency.

## Integration Methods

### Method 1: Custom OpenAI Node (Recommended)

Create a custom Flowise node that uses ReliAPI as the base URL.

#### Step 1: Create Custom Node

Create a file `CustomReliAPIOpenAI.ts`:

```typescript
import { ICommonObject, INode, INodeData, INodeParams } from 'flowise-components'
import { ChatOpenAI } from '@langchain/openai'

class CustomReliAPIOpenAI implements INode {
    label: string
    name: string
    type: string
    icon: string
    category: string
    baseClasses: string[]
    inputs: INodeParams[]

    constructor() {
        this.label = 'ReliAPI OpenAI'
        this.name = 'customReliAPIOpenAI'
        this.type = 'CustomReliAPIOpenAI'
        this.icon = 'openai.svg'
        this.category = 'Chat Models'
        this.baseClasses = ['ChatOpenAI']
        this.inputs = [
            {
                label: 'OpenAI API Key',
                name: 'openaiApiKey',
                type: 'password'
            },
            {
                label: 'RapidAPI Key',
                name: 'rapidApiKey',
                type: 'password'
            },
            {
                label: 'Model Name',
                name: 'modelName',
                type: 'string',
                default: 'gpt-4o-mini'
            },
            {
                label: 'Temperature',
                name: 'temperature',
                type: 'number',
                default: 0.9
            }
        ]
    }

    async init(nodeData: INodeData): Promise<any> {
        const openaiApiKey = nodeData.inputs?.openaiApiKey as string
        const rapidApiKey = nodeData.inputs?.rapidApiKey as string
        const modelName = nodeData.inputs?.modelName as string
        const temperature = nodeData.inputs?.temperature as number

        const model = new ChatOpenAI({
            openAIApiKey: openaiApiKey,
            modelName: modelName,
            temperature: temperature,
            configuration: {
                baseURL: 'https://reliapi.kikuai.dev/proxy/llm',
                defaultHeaders: {
                    'X-RapidAPI-Key': rapidApiKey
                }
            }
        })

        return model
    }
}

module.exports = { nodeClass: CustomReliAPIOpenAI }
```

#### Step 2: Register Node

Add the custom node to Flowise's node registry.

### Method 2: Environment Variable Configuration

Configure Flowise to use ReliAPI via environment variables.

#### Step 1: Set Environment Variables

```bash
export OPENAI_BASE_URL=https://reliapi.kikuai.dev/proxy/llm
export RAPIDAPI_KEY=your-rapidapi-key
```

#### Step 2: Configure Flowise

In Flowise configuration, set:

```json
{
  "OPENAI_BASE_URL": "https://reliapi.kikuai.dev/proxy/llm",
  "RAPIDAPI_KEY": "your-rapidapi-key"
}
```

### Method 3: Proxy Configuration

Use ReliAPI as a proxy in front of Flowise's OpenAI calls.

#### Step 1: Create Proxy Middleware

```typescript
// middleware.ts
export function reliapiProxy(req: Request, res: Response, next: NextFunction) {
  if (req.path.includes('/api/v1/chat')) {
    // Intercept OpenAI API calls
    // Route through ReliAPI
    req.headers['X-RapidAPI-Key'] = process.env.RAPIDAPI_KEY
    req.url = req.url.replace('api.openai.com', 'reliapi.kikuai.dev/proxy/llm')
  }
  next()
}
```

## Benefits

- ✅ **Automatic Caching** - Reduce costs by 50-80%
- ✅ **Idempotency** - Prevent duplicate charges
- ✅ **Automatic Retries** - Handle failures gracefully
- ✅ **Budget Caps** - Prevent surprise bills
- ✅ **Cost Tracking** - See exact cost per request

## Configuration

### Using RapidAPI

```typescript
const model = new ChatOpenAI({
  configuration: {
    baseURL: 'https://reliapi.kikuai.dev/proxy/llm',
    defaultHeaders: {
      'X-RapidAPI-Key': 'your-rapidapi-key'
    }
  }
})
```

### Using Self-Hosted ReliAPI

```typescript
const model = new ChatOpenAI({
  configuration: {
    baseURL: 'http://localhost:8000/proxy/llm',
    defaultHeaders: {
      'Authorization': 'Bearer your-reliapi-key'
    }
  }
})
```

## Example Flow

1. **Create Chat Flow:**
   - Add ReliAPI OpenAI node
   - Configure with RapidAPI key
   - Set model and temperature

2. **Add Caching:**
   - Configure cache TTL in node settings
   - Same prompts will be cached automatically

3. **Monitor Costs:**
   - Check ReliAPI dashboard for cost tracking
   - View cache hit rates

## Troubleshooting

### Node Not Appearing

- Ensure custom node is registered in Flowise
- Check node file is in correct directory
- Restart Flowise server

### Authentication Errors

- Verify RapidAPI key is correct
- Check header name matches (`X-RapidAPI-Key`)
- For self-hosted, use `Authorization` header

### Caching Not Working

- Ensure cache TTL is set
- Check ReliAPI logs for cache hits
- Verify request content is identical

## Additional Resources

- [Flowise Documentation](https://docs.flowiseai.com/)
- [ReliAPI Documentation](https://github.com/KikuAI-Lab/reliapi/wiki)
- [RapidAPI ReliAPI Page](https://rapidapi.com/kikuai-lab-kikuai-lab-default/api/reliapi)

## Support

- [GitHub Issues](https://github.com/KikuAI-Lab/reliapi/issues)
- [Email](mailto:dev@kikuai.dev)














