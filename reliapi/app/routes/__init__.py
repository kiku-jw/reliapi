"""ReliAPI Routes Package.

This package contains all route handlers organized by domain:

Core routes:
- health: Health check and monitoring endpoints
- proxy: HTTP and LLM proxy endpoints
- rapidapi: RapidAPI integration endpoints

Business routes:
- paddle: Paddle payment integration
- onboarding: Self-service API key generation
- analytics: Usage analytics tracking
- calculators: ROI/pricing calculators
- dashboard: Admin dashboard
"""
from reliapi.app.routes import health, proxy, rapidapi

__all__ = ["health", "proxy", "rapidapi"]
