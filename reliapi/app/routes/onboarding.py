"""Self-service onboarding routes.

This module provides automated onboarding flow for new users.
All steps are automated - no manual intervention required.
"""

import os
import secrets
import json
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel, EmailStr, Field
import redis

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# ReliAPI base URL
RELIAPI_BASE_URL = os.getenv("RELIAPI_BASE_URL", "https://reliapi.kikuai.dev")


class OnboardingRequest(BaseModel):
    """Onboarding request model."""

    email: EmailStr
    plan: str = Field(default="free", description="Plan to start with (free, pro, team)")


class OnboardingResponse(BaseModel):
    """Onboarding response model."""

    api_key: str
    quick_start_url: str
    documentation_url: str
    example_code: Dict[str, str]
    integration_status: str


class QuickStartGuide(BaseModel):
    """Quick start guide response."""

    steps: list[Dict[str, Any]]
    code_examples: Dict[str, str]
    test_endpoint: str


@router.post("/start", response_model=OnboardingResponse)
async def start_onboarding(request: OnboardingRequest) -> OnboardingResponse:
    """Start self-service onboarding for a new user.

    This endpoint:
    1. Generates API key automatically
    2. Creates user account
    3. Returns quick start guide and examples
    4. Provides integration verification endpoint

    All steps are automated - no manual intervention required.
    """
    # Generate API key
    api_key = f"reliapi_{secrets.token_urlsafe(32)}"

    # Store user account (in Redis or database)
    redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    user_data = {
        "email": request.email,
        "api_key": api_key,
        "plan": request.plan,
        "created_at": datetime.utcnow().isoformat(),
        "status": "active",
    }

    # Store user
    user_key = f"user:{request.email}"
    redis_client.setex(user_key, 86400 * 365, json.dumps(user_data))  # 1 year TTL

    # Store API key mapping
    api_key_key = f"api_key:{api_key}"
    redis_client.setex(api_key_key, 86400 * 365, json.dumps({"email": request.email}))

    # Generate quick start examples
    example_code = {
        "python": f"""import httpx

# Your ReliAPI endpoint
response = httpx.post(
    "https://reliapi.kikuai.dev/proxy/http",
    headers={{
        "X-API-Key": "{api_key}",
        "Content-Type": "application/json"
    }},
    json={{
        "url": "https://api.openai.com/v1/chat/completions",
        "method": "POST",
        "headers": {{
            "Authorization": "Bearer YOUR_OPENAI_KEY"
        }},
        "body": {{
            "model": "gpt-4o-mini",
            "messages": [{{"role": "user", "content": "Hello!"}}]
        }}
    }}
)
print(response.json())""",
        "javascript": f"""const response = await fetch("https://reliapi.kikuai.dev/proxy/http", {{
    method: "POST",
    headers: {{
        "X-API-Key": "{api_key}",
        "Content-Type": "application/json"
    }},
    body: JSON.stringify({{
        url: "https://api.openai.com/v1/chat/completions",
        method: "POST",
        headers: {{
            Authorization: "Bearer YOUR_OPENAI_KEY"
        }},
        body: {{
            model: "gpt-4o-mini",
            messages: [{{role: "user", content: "Hello!"}}]
        }}
    }})
}});
const data = await response.json();
console.log(data);""",
        "curl": f"""curl -X POST "https://reliapi.kikuai.dev/proxy/http" \\
  -H "X-API-Key: {api_key}" \\
  -H "Content-Type: application/json" \\
  -d '{{
    "url": "https://api.openai.com/v1/chat/completions",
    "method": "POST",
    "headers": {{
      "Authorization": "Bearer YOUR_OPENAI_KEY"
    }},
    "body": {{
      "model": "gpt-4o-mini",
      "messages": [{{"role": "user", "content": "Hello!"}}]
    }}
  }}' """,
    }

    return OnboardingResponse(
        api_key=api_key,
        quick_start_url=f"{RELIAPI_BASE_URL}/onboarding/quick-start",
        documentation_url="https://github.com/kiku-jw/reliapi",
        example_code=example_code,
        integration_status="pending_verification",
    )


@router.get("/quick-start", response_model=QuickStartGuide)
async def get_quick_start_guide() -> QuickStartGuide:
    """Get quick start guide for onboarding.

    Returns step-by-step guide with code examples.
    """
    steps = [
        {
            "step": 1,
            "title": "Get your API key",
            "description": "Use the /onboarding/start endpoint to get your API key",
        },
        {
            "step": 2,
            "title": "Make your first request",
            "description": "Use the /proxy/http endpoint to proxy any HTTP API call",
        },
        {
            "step": 3,
            "title": "Enable features",
            "description": "Add retry logic, caching, and idempotency to your requests",
        },
        {
            "step": 4,
            "title": "Verify integration",
            "description": "Use /onboarding/verify to check your integration status",
        },
    ]

    code_examples = {
        "python": "See /onboarding/start for Python example",
        "javascript": "See /onboarding/start for JavaScript example",
        "curl": "See /onboarding/start for curl example",
    }

    return QuickStartGuide(
        steps=steps,
        code_examples=code_examples,
        test_endpoint=f"{RELIAPI_BASE_URL}/proxy/http",
    )


@router.post("/verify")
async def verify_integration(
    api_key: str = Header(..., alias="X-API-Key"),
) -> Dict[str, Any]:
    """Verify user integration.

    Checks if user has made successful API calls and provides feedback.
    """
    from datetime import datetime, timedelta

    redis_client = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))

    # Get user from API key
    api_key_key = f"api_key:{api_key}"
    user_data = redis_client.get(api_key_key)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid API key")

    user_info = json.loads(user_data)
    email = user_info.get("email")

    # Check usage in last 24 hours
    now = datetime.utcnow()
    usage_key = f"usage:{email}:{now.year}-{now.month:02d}"
    requests_count = int(redis_client.get(usage_key) or 0)

    # Check if user has made at least one successful request
    has_made_request = requests_count > 0

    verification_status = "verified" if has_made_request else "pending"

    return {
        "status": verification_status,
        "requests_made": requests_count,
        "message": "Integration verified!"
        if has_made_request
        else "Make your first API call to verify integration",
        "next_steps": [
            "Try the /proxy/http endpoint",
            "Check out the documentation",
            "Explore advanced features",
        ]
        if has_made_request
        else [
            "Make your first API call using the examples",
            "Check the /onboarding/quick-start guide",
        ],
    }
