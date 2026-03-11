"""Tests for Multi-Tenant functionality."""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from reliapi.app.dependencies import verify_api_key
from reliapi.config.loader import ConfigLoader
from reliapi.config.schema import ReliAPIConfig, TenantConfig
from reliapi.core.cache import Cache
from reliapi.core.idempotency import IdempotencyManager


class TestMultiTenantCacheIsolation:
    """Test cache isolation between tenants."""

    @patch("reliapi.core.cache.redis")
    def test_cache_keys_isolated_by_tenant(self, mock_redis_module, mock_redis):
        """Test that cache keys are prefixed with tenant name."""
        mock_redis_module.from_url.return_value = mock_redis
        cache = Cache("redis://localhost:6379/0")

        # Set cache for tenant-a
        cache.set(
            "GET",
            "https://example.com/api",
            None,
            None,
            {"data": "tenant-a-data"},
            ttl_s=60,
            tenant="tenant-a",
        )

        # Set cache for tenant-b
        cache.set(
            "GET",
            "https://example.com/api",
            None,
            None,
            {"data": "tenant-b-data"},
            ttl_s=60,
            tenant="tenant-b",
        )

        # Verify different keys were used
        calls = mock_redis.setex.call_args_list
        assert len(calls) == 2

        key_a = calls[0][0][0]
        key_b = calls[1][0][0]

        assert "tenant:tenant-a" in key_a
        assert "tenant:tenant-b" in key_b
        assert key_a != key_b

    @patch("reliapi.core.cache.redis")
    def test_cache_get_isolated_by_tenant(self, mock_redis_module, mock_redis):
        """Test that cache.get returns correct data per tenant."""
        mock_redis_module.from_url.return_value = mock_redis
        cache = Cache("redis://localhost:6379/0")

        # Mock tenant-a cache hit
        mock_redis.get.return_value = json.dumps({"data": "tenant-a-data"})
        result_a = cache.get("GET", "https://example.com/api", None, None, None, tenant="tenant-a")
        assert result_a == {"data": "tenant-a-data"}

        # Mock tenant-b cache hit (different data)
        mock_redis.get.return_value = json.dumps({"data": "tenant-b-data"})
        result_b = cache.get("GET", "https://example.com/api", None, None, None, tenant="tenant-b")
        assert result_b == {"data": "tenant-b-data"}

        # Verify get was called with tenant-specific keys
        calls = mock_redis.get.call_args_list
        assert len(calls) == 2
        assert "tenant:tenant-a" in calls[0][0][0]
        assert "tenant:tenant-b" in calls[1][0][0]

    @patch("reliapi.core.cache.redis")
    def test_cache_no_tenant_isolation(self, mock_redis_module, mock_redis):
        """Test that cache without tenant uses default namespace."""
        mock_redis_module.from_url.return_value = mock_redis
        cache = Cache("redis://localhost:6379/0")

        cache.set("GET", "https://example.com/api", None, None, {"data": "default"}, ttl_s=60)

        call = mock_redis.setex.call_args
        key = call[0][0]

        # Should not have tenant prefix
        assert "tenant:" not in key
        assert "reliapi:cache:" in key


class TestMultiTenantIdempotencyIsolation:
    """Test idempotency isolation between tenants."""

    @patch("reliapi.core.idempotency.redis")
    def test_idempotency_keys_isolated_by_tenant(self, mock_redis_module, mock_redis):
        """Test that idempotency keys are prefixed with tenant name."""
        mock_redis_module.from_url.return_value = mock_redis
        manager = IdempotencyManager("redis://localhost:6379/0")

        # Mock SET to return True (new request)
        mock_redis.set.return_value = True
        mock_redis.get.return_value = None

        # Register request for tenant-a
        manager.register_request(
            "key-123", "POST", "https://example.com/api", None, b"body", "req-1", tenant="tenant-a"
        )

        # Register request for tenant-b (same idempotency key)
        manager.register_request(
            "key-123", "POST", "https://example.com/api", None, b"body", "req-2", tenant="tenant-b"
        )

        # Verify different keys were used
        calls = mock_redis.get.call_args_list
        assert len(calls) >= 2

        key_a = calls[0][0][0]
        key_b = calls[1][0][0]

        assert "tenant:tenant-a" in key_a
        assert "tenant:tenant-b" in key_b
        assert key_a != key_b

    @patch("reliapi.core.idempotency.redis")
    def test_idempotency_result_isolated_by_tenant(self, mock_redis_module, mock_redis):
        """Test that idempotency results are isolated per tenant."""
        mock_redis_module.from_url.return_value = mock_redis
        manager = IdempotencyManager("redis://localhost:6379/0")

        # Store result for tenant-a
        result_a = {"status": "success", "data": "tenant-a-result"}
        manager.store_result("key-123", result_a, ttl_s=60, tenant="tenant-a")

        # Store result for tenant-b (same key, different data)
        result_b = {"status": "success", "data": "tenant-b-result"}
        manager.store_result("key-123", result_b, ttl_s=60, tenant="tenant-b")

        # Verify different keys were used
        calls = mock_redis.setex.call_args_list
        assert len(calls) == 2

        key_a = calls[0][0][0]
        key_b = calls[1][0][0]

        assert "tenant:tenant-a" in key_a
        assert "tenant:tenant-b" in key_b
        assert key_a != key_b

    @patch("reliapi.core.idempotency.redis")
    def test_idempotency_get_result_isolated(self, mock_redis_module, mock_redis):
        """Test that get_result returns correct data per tenant."""
        mock_redis_module.from_url.return_value = mock_redis
        manager = IdempotencyManager("redis://localhost:6379/0")

        # Mock tenant-a result
        mock_redis.get.return_value = json.dumps({"data": "tenant-a-result"})
        result_a = manager.get_result("key-123", tenant="tenant-a")
        assert result_a == {"data": "tenant-a-result"}

        # Mock tenant-b result (different data)
        mock_redis.get.return_value = json.dumps({"data": "tenant-b-result"})
        result_b = manager.get_result("key-123", tenant="tenant-b")
        assert result_b == {"data": "tenant-b-result"}

        # Verify get was called with tenant-specific keys
        calls = mock_redis.get.call_args_list
        assert len(calls) == 2
        assert "tenant:tenant-a" in calls[0][0][0]
        assert "tenant:tenant-b" in calls[1][0][0]

    @patch("reliapi.core.idempotency.redis")
    def test_idempotency_in_progress_isolated(self, mock_redis_module, mock_redis):
        """Test that in_progress markers are isolated per tenant."""
        mock_redis_module.from_url.return_value = mock_redis
        manager = IdempotencyManager("redis://localhost:6379/0")

        # Mark tenant-a as in progress
        manager.mark_in_progress("key-123", ttl_s=300, tenant="tenant-a")

        # Mark tenant-b as in progress (same key)
        manager.mark_in_progress("key-123", ttl_s=300, tenant="tenant-b")

        # Verify different keys were used
        calls = mock_redis.setex.call_args_list
        assert len(calls) == 2

        key_a = calls[0][0][0]
        key_b = calls[1][0][0]

        assert "tenant:tenant-a" in key_a
        assert "tenant:tenant-b" in key_b
        assert key_a != key_b


class TestMultiTenantConfig:
    """Test multi-tenant configuration loading."""

    def test_load_tenants_from_config(self):
        """Test loading tenants from config.yaml."""
        config_yaml = """
targets:
  openai:
    base_url: https://api.openai.com/v1
    llm:
      provider: openai

tenants:
  - name: "client-a"
    api_key: "sk-client-a-123"
    budget_caps:
      openai:
        soft_cost_cap_usd: 10.0
        hard_cost_cap_usd: 50.0
    fallback_targets:
      openai: ["openai-secondary"]
    rate_limit_rpm: 1000

  - name: "client-b"
    api_key: "sk-client-b-456"
    budget_caps:
      openai:
        soft_cost_cap_usd: 5.0
        hard_cost_cap_usd: 20.0
    rate_limit_rpm: 500

  - name: "client-c"
    api_key: "sk-client-c-789"
    budget_caps:
      openai:
        soft_cost_cap_usd: 1.0
        hard_cost_cap_usd: 5.0
    fallback_targets:
      openai: ["anthropic-backup"]
    rate_limit_rpm: 100
"""
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = config_yaml
            loader = ConfigLoader("config.yaml")
            config = loader.load()

            assert config.tenants is not None
            assert len(config.tenants) == 3

            # Verify tenant-a
            tenant_a = loader.get_tenant("client-a")
            assert tenant_a is not None
            assert tenant_a.api_key == "sk-client-a-123"
            assert tenant_a.budget_caps["openai"]["soft_cost_cap_usd"] == 10.0
            assert tenant_a.budget_caps["openai"]["hard_cost_cap_usd"] == 50.0
            assert tenant_a.rate_limit_rpm == 1000

            # Verify tenant-b
            tenant_b = loader.get_tenant("client-b")
            assert tenant_b is not None
            assert tenant_b.api_key == "sk-client-b-456"
            assert tenant_b.budget_caps["openai"]["soft_cost_cap_usd"] == 5.0
            assert tenant_b.budget_caps["openai"]["hard_cost_cap_usd"] == 20.0
            assert tenant_b.rate_limit_rpm == 500

            # Verify tenant-c
            tenant_c = loader.get_tenant("client-c")
            assert tenant_c is not None
            assert tenant_c.api_key == "sk-client-c-789"
            assert tenant_c.budget_caps["openai"]["soft_cost_cap_usd"] == 1.0
            assert tenant_c.budget_caps["openai"]["hard_cost_cap_usd"] == 5.0
            assert tenant_c.rate_limit_rpm == 100

    def test_find_tenant_by_api_key(self):
        """Test finding tenant by API key."""
        config_yaml = """
targets:
  openai:
    base_url: https://api.openai.com/v1
    llm:
      provider: openai

tenants:
  - name: "client-a"
    api_key: "sk-client-a-123"
  - name: "client-b"
    api_key: "sk-client-b-456"
"""
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = config_yaml
            loader = ConfigLoader("config.yaml")
            config = loader.load()

            # Find tenant by API key
            tenant_a = loader.find_tenant_by_api_key("sk-client-a-123")
            assert tenant_a is not None
            assert tenant_a.name == "client-a"

            tenant_b = loader.find_tenant_by_api_key("sk-client-b-456")
            assert tenant_b is not None
            assert tenant_b.name == "client-b"

            # Non-existent key
            tenant_none = loader.find_tenant_by_api_key("sk-invalid")
            assert tenant_none is None


class TestMultiTenantBudgetCaps:
    """Test tenant-specific budget caps."""

    def test_tenant_budget_caps_override(self):
        """Test that tenant budget caps override target defaults."""
        config_yaml = """
targets:
  openai:
    base_url: https://api.openai.com/v1
    llm:
      provider: openai
      soft_cost_cap_usd: 1.0
      hard_cost_cap_usd: 10.0

tenants:
  - name: "premium-client"
    api_key: "sk-premium-123"
    budget_caps:
      openai:
        soft_cost_cap_usd: 100.0
        hard_cost_cap_usd: 500.0
"""
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = config_yaml
            loader = ConfigLoader("config.yaml")
            config = loader.load()

            tenant = loader.get_tenant("premium-client")
            assert tenant is not None

            # Tenant should have higher caps than default
            assert tenant.budget_caps["openai"]["soft_cost_cap_usd"] == 100.0
            assert tenant.budget_caps["openai"]["hard_cost_cap_usd"] == 500.0


class TestMultiTenantFallbackChains:
    """Test tenant-specific fallback chains."""

    def test_tenant_fallback_chains(self):
        """Test that tenants can have different fallback chains."""
        config_yaml = """
targets:
  openai-primary:
    base_url: https://api.openai.com/v1
    llm:
      provider: openai
  openai-secondary:
    base_url: https://api.openai.com/v1
    llm:
      provider: openai
  anthropic-backup:
    base_url: https://api.anthropic.com/v1
    llm:
      provider: anthropic

tenants:
  - name: "client-a"
    api_key: "sk-client-a-123"
    fallback_targets:
      openai-primary: ["openai-secondary", "anthropic-backup"]
  
  - name: "client-b"
    api_key: "sk-client-b-456"
    fallback_targets:
      openai-primary: ["anthropic-backup"]
"""
        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = config_yaml
            loader = ConfigLoader("config.yaml")
            config = loader.load()

            tenant_a = loader.get_tenant("client-a")
            tenant_b = loader.get_tenant("client-b")

            # Client A has longer fallback chain
            assert len(tenant_a.fallback_targets["openai-primary"]) == 2
            assert "openai-secondary" in tenant_a.fallback_targets["openai-primary"]
            assert "anthropic-backup" in tenant_a.fallback_targets["openai-primary"]

            # Client B has shorter fallback chain
            assert len(tenant_b.fallback_targets["openai-primary"]) == 1
            assert "anthropic-backup" in tenant_b.fallback_targets["openai-primary"]


class TestMultiTenantIntegration:
    """Integration tests for multi-tenant functionality."""

    @patch("reliapi.core.cache.redis")
    @patch("reliapi.core.idempotency.redis")
    def test_three_tenants_different_restrictions(
        self, mock_idempotency_redis_module, mock_cache_redis_module, mock_redis
    ):
        """Test three tenants with different restrictions (cache, idempotency, budget)."""
        mock_cache_redis_module.from_url.return_value = mock_redis
        mock_idempotency_redis_module.from_url.return_value = mock_redis
        # Setup cache and idempotency managers
        cache = Cache("redis://localhost:6379/0")
        idempotency = IdempotencyManager("redis://localhost:6379/0")

        # Tenant 1: Premium (high budget, long fallback)
        cache.set(
            "GET",
            "https://api.example.com/data",
            None,
            None,
            {"data": "premium-data"},
            ttl_s=3600,
            tenant="premium",
        )
        idempotency.store_result(
            "req-123", {"result": "premium-result"}, ttl_s=3600, tenant="premium"
        )

        # Tenant 2: Standard (medium budget, short fallback)
        cache.set(
            "GET",
            "https://api.example.com/data",
            None,
            None,
            {"data": "standard-data"},
            ttl_s=1800,
            tenant="standard",
        )
        idempotency.store_result(
            "req-123", {"result": "standard-result"}, ttl_s=1800, tenant="standard"
        )

        # Tenant 3: Free (low budget, no fallback)
        cache.set(
            "GET",
            "https://api.example.com/data",
            None,
            None,
            {"data": "free-data"},
            ttl_s=600,
            tenant="free",
        )
        idempotency.store_result("req-123", {"result": "free-result"}, ttl_s=600, tenant="free")

        # Verify isolation: same idempotency key, different results per tenant
        mock_redis.get.return_value = json.dumps({"result": "premium-result"})
        result_premium = idempotency.get_result("req-123", tenant="premium")
        assert result_premium == {"result": "premium-result"}

        mock_redis.get.return_value = json.dumps({"result": "standard-result"})
        result_standard = idempotency.get_result("req-123", tenant="standard")
        assert result_standard == {"result": "standard-result"}

        mock_redis.get.return_value = json.dumps({"result": "free-result"})
        result_free = idempotency.get_result("req-123", tenant="free")
        assert result_free == {"result": "free-result"}

        # Verify all keys are different
        calls = mock_redis.setex.call_args_list
        keys = [call[0][0] for call in calls]

        # Should have 6 keys total (3 cache + 3 idempotency)
        assert len(keys) == 6

        # All keys should be unique
        assert len(set(keys)) == 6

        # Each tenant should have its own namespace
        premium_keys = [k for k in keys if "tenant:premium" in k]
        standard_keys = [k for k in keys if "tenant:standard" in k]
        free_keys = [k for k in keys if "tenant:free" in k]

        assert len(premium_keys) == 2
        assert len(standard_keys) == 2
        assert len(free_keys) == 2


class TestMultiTenantAuth:
    """Test multi-tenant authentication paths."""

    def test_verify_api_key_reads_tenants_via_loader_helper(self):
        """Regression test for config loader compatibility in multi-tenant auth."""
        tenant = TenantConfig(name="client-a", api_key="sk-client-a-123")
        config_loader = Mock()
        config_loader.config = {"tenants": {"client-a": {"api_key": tenant.api_key}}}
        config_loader.get_tenants.return_value = {"client-a": tenant}

        state = SimpleNamespace(
            config_loader=config_loader,
            rapidapi_client=None,
            rate_limiter=None,
            rapidapi_tenant_manager=None,
        )
        request = Mock()
        request.headers = {"X-API-Key": tenant.api_key}
        request.state = SimpleNamespace()

        with patch("reliapi.app.dependencies.get_app_state", return_value=state):
            api_key, tenant_name, tier = verify_api_key(request)

        assert api_key == tenant.api_key
        assert tenant_name == "client-a"
        assert tier == "free"
        assert request.state.tenant == "client-a"
        assert request.state.tier == "free"
