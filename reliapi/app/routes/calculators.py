"""Pricing and ROI calculators.

This module provides calculator endpoints for pricing, ROI, and cost savings.
All calculations are automated - no manual intervention required.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter(prefix="/calculators", tags=["calculators"])


class PricingCalculatorRequest(BaseModel):
    """Pricing calculator request model."""

    monthly_requests: int = Field(ge=0, description="Monthly API requests")
    cost_per_request: float = Field(ge=0, description="Average cost per request (USD)")
    cache_hit_rate: float = Field(ge=0, le=100, description="Cache hit rate (%)")


class PricingCalculatorResponse(BaseModel):
    """Pricing calculator response model."""

    cost_without_reliapi: float
    cost_with_reliapi: float
    monthly_savings: float
    savings_percentage: float
    reliapi_cost: float
    net_savings: float
    recommended_plan: str


class ROICalculatorRequest(BaseModel):
    """ROI calculator request model."""

    current_monthly_cost: float = Field(ge=0, description="Current monthly API costs (USD)")
    expected_cache_hit_rate: float = Field(ge=0, le=100, description="Expected cache hit rate (%)")
    team_size: int = Field(ge=1, description="Team size")
    development_time_saved_hours: float = Field(ge=0, description="Development time saved (hours/month)")
    hourly_rate: float = Field(ge=0, default=100, description="Developer hourly rate (USD)")


class ROICalculatorResponse(BaseModel):
    """ROI calculator response model."""

    monthly_cost_savings: float
    annual_cost_savings: float
    development_time_savings_usd: float
    total_monthly_savings: float
    total_annual_savings: float
    reliapi_monthly_cost: float
    net_monthly_savings: float
    net_annual_savings: float
    roi_percentage: float
    payback_period_months: float


@router.post("/pricing", response_model=PricingCalculatorResponse)
async def calculate_pricing(request: PricingCalculatorRequest) -> PricingCalculatorResponse:
    """Calculate pricing and savings with ReliAPI.

    Automatically calculates:
    - Cost without ReliAPI
    - Cost with ReliAPI (considering cache hits)
    - Monthly savings
    - Recommended plan
    """
    # Calculate cost without ReliAPI
    cost_without = request.monthly_requests * request.cost_per_request

    # Calculate cost with ReliAPI (cache hits don't incur API costs)
    cache_hits = int(request.monthly_requests * (request.cache_hit_rate / 100))
    actual_requests = request.monthly_requests - cache_hits
    cost_with = actual_requests * request.cost_per_request

    # Determine ReliAPI plan cost
    if request.monthly_requests <= 10000:
        reliapi_cost = 0  # Free
        plan = "free"
    elif request.monthly_requests <= 100000:
        reliapi_cost = 49  # Pro
        plan = "pro"
    elif request.monthly_requests <= 500000:
        reliapi_cost = 199  # Team
        plan = "team"
    else:
        reliapi_cost = 199 + ((request.monthly_requests - 500000) // 1000000) * 100  # Enterprise estimate
        plan = "enterprise"

    monthly_savings = cost_without - cost_with
    savings_percentage = (monthly_savings / cost_without * 100) if cost_without > 0 else 0
    net_savings = monthly_savings - reliapi_cost

    return PricingCalculatorResponse(
        cost_without_reliapi=cost_without,
        cost_with_reliapi=cost_with,
        monthly_savings=monthly_savings,
        savings_percentage=round(savings_percentage, 2),
        reliapi_cost=reliapi_cost,
        net_savings=net_savings,
        recommended_plan=plan,
    )


@router.post("/roi", response_model=ROICalculatorResponse)
async def calculate_roi(request: ROICalculatorRequest) -> ROICalculatorResponse:
    """Calculate ROI for ReliAPI implementation.

    Automatically calculates:
    - Cost savings from caching
    - Development time savings
    - Total ROI
    - Payback period
    """
    # Calculate cost savings from caching
    cache_hits_value = request.current_monthly_cost * (request.expected_cache_hit_rate / 100)
    monthly_cost_savings = cache_hits_value
    annual_cost_savings = monthly_cost_savings * 12

    # Calculate development time savings
    development_time_savings_usd = request.development_time_saved_hours * request.hourly_rate

    # Total savings
    total_monthly_savings = monthly_cost_savings + development_time_savings_usd
    total_annual_savings = total_monthly_savings * 12

    # Determine ReliAPI cost based on usage
    # Estimate requests from current cost (assuming $0.001 per request average)
    estimated_requests = int(request.current_monthly_cost / 0.001)
    if estimated_requests <= 10000:
        reliapi_monthly_cost = 0
    elif estimated_requests <= 100000:
        reliapi_monthly_cost = 49
    elif estimated_requests <= 500000:
        reliapi_monthly_cost = 199
    else:
        reliapi_monthly_cost = 199 + ((estimated_requests - 500000) // 1000000) * 100

    net_monthly_savings = total_monthly_savings - reliapi_monthly_cost
    net_annual_savings = net_monthly_savings * 12

    # Calculate ROI
    reliapi_annual_cost = reliapi_monthly_cost * 12
    roi_percentage = (
        (net_annual_savings / reliapi_annual_cost * 100) if reliapi_annual_cost > 0 else 0
    )

    # Payback period (months)
    payback_period = (
        (reliapi_monthly_cost / net_monthly_savings) if net_monthly_savings > 0 else 0
    )

    return ROICalculatorResponse(
        monthly_cost_savings=round(monthly_cost_savings, 2),
        annual_cost_savings=round(annual_cost_savings, 2),
        development_time_savings_usd=round(development_time_savings_usd, 2),
        total_monthly_savings=round(total_monthly_savings, 2),
        total_annual_savings=round(total_annual_savings, 2),
        reliapi_monthly_cost=reliapi_monthly_cost,
        net_monthly_savings=round(net_monthly_savings, 2),
        net_annual_savings=round(net_annual_savings, 2),
        roi_percentage=round(roi_percentage, 2),
        payback_period_months=round(payback_period, 2),
    )


@router.get("/cost-savings")
async def calculate_cost_savings(
    monthly_requests: int,
    cost_per_request: float = 0.001,
    cache_hit_rate: float = 60,
) -> dict:
    """Calculate cost savings from caching.

    Simplified endpoint for quick calculations.
    """
    cost_without = monthly_requests * cost_per_request
    cache_hits = int(monthly_requests * (cache_hit_rate / 100))
    actual_requests = monthly_requests - cache_hits
    cost_with = actual_requests * cost_per_request
    savings = cost_without - cost_with
    savings_percentage = (savings / cost_without * 100) if cost_without > 0 else 0

    return {
        "monthly_requests": monthly_requests,
        "cache_hit_rate": cache_hit_rate,
        "cost_without_caching": round(cost_without, 2),
        "cost_with_caching": round(cost_with, 2),
        "monthly_savings": round(savings, 2),
        "annual_savings": round(savings * 12, 2),
        "savings_percentage": round(savings_percentage, 2),
    }

