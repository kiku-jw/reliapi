"""YAML configuration loader for routes-based ReliAPI."""
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from reliapi.config.schema import ReliAPIConfig


class ConfigLoader:
    """Load and parse ReliAPI routes-based configuration."""

    def __init__(self, config_path: str):
        """
        Args:
            config_path: Path to YAML configuration file
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self.model: Optional[ReliAPIConfig] = None

    def _normalize_tenants(self, raw_config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize tenants to the dict form expected by the schema."""
        tenants = raw_config.get("tenants")
        if isinstance(tenants, list):
            normalized_tenants: Dict[str, Any] = {}
            for tenant in tenants:
                if not isinstance(tenant, dict):
                    continue
                tenant_name = tenant.get("name")
                if not tenant_name:
                    raise ValueError("Each tenant entry must include a name")
                normalized_tenants[tenant_name] = tenant
            raw_config["tenants"] = normalized_tenants
        return raw_config

    def load(self) -> ReliAPIConfig:
        """Load and validate configuration from YAML file."""
        try:
            with open(self.config_path, "r") as f:
                raw_config = yaml.safe_load(f.read()) or {}
        except FileNotFoundError as e:
            raise FileNotFoundError(f"Config file not found: {self.config_path}") from e
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML syntax in {self.config_path}: {e}") from e

        raw_config = self._normalize_tenants(raw_config)

        # Validate through Pydantic
        try:
            self.model = ReliAPIConfig(**raw_config)
            # Convert back to dict for compatibility
            self.config = self.model.model_dump(exclude_none=True)
        except Exception as e:
            raise ValueError(f"Configuration validation failed in {self.config_path}: {e}") from e

        return self.model

    def get_targets(self) -> Dict[str, Dict[str, Any]]:
        """Get targets configuration (new schema)."""
        return self.config.get("targets", {})

    def get_upstreams(self) -> Dict[str, Dict[str, Any]]:
        """Get upstreams configuration (legacy, maps to targets)."""
        return self.config.get("upstreams", self.get_targets())

    def get_routes(self) -> List[Dict[str, Any]]:
        """Get routes configuration."""
        return self.config.get("routes", [])

    def get_target(self, name: str) -> Optional[Dict[str, Any]]:
        """Get specific target configuration."""
        return self.get_targets().get(name)

    def get_tenants(self) -> Optional[Dict[str, Any]]:
        """Get tenants configuration."""
        if self.model:
            return self.model.tenants
        return self.config.get("tenants")

    def get_tenant(self, tenant_name: str) -> Optional[Any]:
        """Get specific tenant configuration."""
        tenants = self.get_tenants()
        if not tenants:
            return None
        return tenants.get(tenant_name)

    def find_tenant_by_api_key(self, api_key: str) -> Optional[Any]:
        """Find tenant name by API key.

        Returns:
            Tenant config if found, None otherwise
        """
        tenants = self.get_tenants()
        if not tenants:
            return None

        for tenant_config in tenants.values():
            tenant_api_key = (
                tenant_config.api_key
                if hasattr(tenant_config, "api_key")
                else tenant_config.get("api_key")
            )
            if tenant_api_key == api_key:
                return tenant_config

        return None

    def get_provider_key_pools(self) -> Optional[Dict[str, Any]]:
        """Get provider key pools configuration."""
        return self.config.get("provider_key_pools")

    def get_client_profiles(self) -> Optional[Dict[str, Any]]:
        """Get client profiles configuration."""
        return self.config.get("client_profiles")

    def get_upstream(self, name: str) -> Optional[Dict[str, Any]]:
        """Get specific upstream configuration (legacy)."""
        return self.get_target(name) or self.get_upstreams().get(name)

    def find_route(self, method: str, path: str) -> Optional[Dict[str, Any]]:
        """
        Find matching route for method and path.

        Args:
            method: HTTP method
            path: Request path

        Returns:
            Route configuration or None
        """
        for route in self.get_routes():
            match = route.get("match", {})
            route_path = match.get("path", "")
            route_methods = match.get("methods", [])

            # Simple path matching (supports ** wildcard)
            if self._path_matches(path, route_path) and (
                not route_methods or method.upper() in [m.upper() for m in route_methods]
            ):
                return route

        return None

    def _path_matches(self, request_path: str, route_path: str) -> bool:
        """Simple path matching with ** wildcard support."""
        if route_path == request_path:
            return True

        # Support ** wildcard
        if "**" in route_path:
            prefix = route_path.replace("**", "")
            return request_path.startswith(prefix)

        # Support * single segment wildcard
        if "*" in route_path:
            import re

            pattern = route_path.replace("*", "[^/]+")
            return bool(re.match(pattern, request_path))

        return False
