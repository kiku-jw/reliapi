#!/usr/bin/env python3
"""Generate OpenAPI specification from FastAPI application.

Reads configuration from product.yaml and extracts the OpenAPI schema
from the ReliAPI FastAPI application.
"""

import importlib
import json
import sys
from pathlib import Path

import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_config() -> dict:
    """Load product.yaml configuration."""
    config_path = PROJECT_ROOT / "product.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path) as f:
        return yaml.safe_load(f)


def get_fastapi_app(app_import: str):
    """Import and return the FastAPI application.

    Args:
        app_import: Import path in format "module.path:app_variable"

    Returns:
        FastAPI application instance
    """
    module_path, app_name = app_import.split(":")
    module = importlib.import_module(module_path)
    return getattr(module, app_name)


def generate_openapi(config: dict) -> dict:
    """Generate OpenAPI schema from FastAPI app.

    Args:
        config: Product configuration dictionary

    Returns:
        OpenAPI schema dictionary
    """
    openapi_config = config.get("openapi", {})
    app_import = openapi_config.get("app_import", "reliapi.app.main:app")

    print(f"Importing FastAPI app from: {app_import}")
    app = get_fastapi_app(app_import)

    # Get OpenAPI schema
    openapi_schema = app.openapi()

    # Update metadata from product.yaml
    product = config.get("product", {})
    openapi_schema["info"]["title"] = product.get("display_name", openapi_schema["info"]["title"])
    openapi_schema["info"]["description"] = product.get(
        "description", openapi_schema["info"]["description"]
    )
    openapi_schema["info"]["version"] = product.get("version", openapi_schema["info"]["version"])

    # Add contact info
    public_repo = config.get("repos", {}).get("public", "kiku-jw/reliapi")
    openapi_schema["info"]["contact"] = {
        "name": "KikuAI-Lab",
        "url": f"https://github.com/{public_repo}",
        "email": "dev@kikuai.dev",
    }

    # Add license info
    openapi_schema["info"]["license"] = {
        "name": "AGPL-3.0-only",
        "url": "https://www.gnu.org/licenses/agpl-3.0.html",
    }

    return openapi_schema


def validate_openapi(schema: dict) -> bool:
    """Validate OpenAPI schema structure.

    Args:
        schema: OpenAPI schema dictionary

    Returns:
        True if valid, raises exception otherwise
    """
    required_fields = ["openapi", "info", "paths"]
    for field in required_fields:
        if field not in schema:
            raise ValueError(f"Missing required OpenAPI field: {field}")

    info_fields = ["title", "version"]
    for field in info_fields:
        if field not in schema["info"]:
            raise ValueError(f"Missing required info field: {field}")

    print("OpenAPI schema validation passed")
    return True


def write_openapi(schema: dict, output_path: Path) -> None:
    """Write OpenAPI schema to YAML file.

    Args:
        schema: OpenAPI schema dictionary
        output_path: Path to output file
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        yaml.dump(schema, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    print(f"OpenAPI schema written to: {output_path}")

    # Also write JSON version for tools that prefer JSON
    json_path = output_path.with_suffix(".json")
    with open(json_path, "w") as f:
        json.dump(schema, f, indent=2)
    print(f"OpenAPI JSON written to: {json_path}")


def main() -> int:
    """Main entry point."""
    try:
        print("Loading configuration...")
        config = load_config()

        print("Generating OpenAPI schema...")
        schema = generate_openapi(config)

        print("Validating OpenAPI schema...")
        validate_openapi(schema)

        output_path = PROJECT_ROOT / config.get("openapi", {}).get("output", "openapi/openapi.yaml")
        write_openapi(schema, output_path)

        print("OpenAPI generation complete!")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
