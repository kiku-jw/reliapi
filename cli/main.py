#!/usr/bin/env python3
"""ReliAPI CLI client.

Provides commands to interact with ReliAPI from the terminal.
Uses the SDK/HTTP client under the hood for all API calls.

Commands:
    reli ping       - Check API health status
    reli info       - Show CLI and API information
    reli request    - Make a proxied HTTP request
"""

import json
import sys
from pathlib import Path
from typing import Optional

import click
import httpx
from rich.console import Console
from rich.table import Table

# Try to load version from product.yaml
try:
    import yaml

    config_path = Path(__file__).parent.parent / "product.yaml"
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f)
        VERSION = config["product"]["version"]
        PRODUCT_NAME = config["product"]["name"]
    else:
        VERSION = "1.0.0"
        PRODUCT_NAME = "reliapi"
except Exception:
    VERSION = "1.0.0"
    PRODUCT_NAME = "reliapi"

console = Console()


class ReliAPIClient:
    """HTTP client for ReliAPI."""

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make GET request."""
        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()

    def post(self, path: str, data: dict) -> dict:
        """Make POST request."""
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}{path}",
                json=data,
                headers=self._headers(),
            )
            response.raise_for_status()
            return response.json()


def get_client(ctx: click.Context) -> ReliAPIClient:
    """Get API client from context."""
    return ReliAPIClient(
        base_url=ctx.obj["base_url"],
        api_key=ctx.obj.get("api_key"),
        timeout=ctx.obj.get("timeout", 30.0),
    )


@click.group()
@click.option(
    "--base-url",
    "-u",
    envvar="RELIAPI_URL",
    default="http://localhost:8000",
    help="ReliAPI base URL",
)
@click.option(
    "--api-key",
    "-k",
    envvar="RELIAPI_API_KEY",
    default=None,
    help="API key for authentication",
)
@click.option(
    "--timeout",
    "-t",
    default=30.0,
    help="Request timeout in seconds",
)
@click.option(
    "--output",
    "-o",
    type=click.Choice(["json", "table", "plain"]),
    default="table",
    help="Output format",
)
@click.version_option(VERSION, prog_name="reli")
@click.pass_context
def cli(
    ctx: click.Context,
    base_url: str,
    api_key: Optional[str],
    timeout: float,
    output: str,
) -> None:
    """ReliAPI CLI - Reliability layer for API calls.

    Configure the API URL via --base-url or RELIAPI_URL environment variable.
    """
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url
    ctx.obj["api_key"] = api_key
    ctx.obj["timeout"] = timeout
    ctx.obj["output"] = output


@cli.command()
@click.pass_context
def ping(ctx: click.Context) -> None:
    """Check API health status."""
    client = get_client(ctx)
    output_format = ctx.obj["output"]

    try:
        result = client.get("/healthz")

        if output_format == "json":
            console.print_json(json.dumps(result))
        elif output_format == "table":
            table = Table(title="Health Check")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="green")
            for key, value in result.items():
                table.add_row(key, str(value))
            console.print(table)
        else:
            console.print(f"Status: {result.get('status', 'unknown')}")

    except httpx.HTTPError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.pass_context
def info(ctx: click.Context) -> None:
    """Show CLI and API information."""
    output_format = ctx.obj["output"]

    info_data = {
        "cli_version": VERSION,
        "product_name": PRODUCT_NAME,
        "base_url": ctx.obj["base_url"],
        "has_api_key": ctx.obj.get("api_key") is not None,
    }

    # Try to get API info
    try:
        client = get_client(ctx)
        health = client.get("/healthz")
        info_data["api_status"] = health.get("status", "unknown")
    except Exception:
        info_data["api_status"] = "unreachable"

    if output_format == "json":
        console.print_json(json.dumps(info_data))
    elif output_format == "table":
        table = Table(title="ReliAPI CLI Info")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        for key, value in info_data.items():
            table.add_row(key.replace("_", " ").title(), str(value))
        console.print(table)
    else:
        for key, value in info_data.items():
            console.print(f"{key.replace('_', ' ').title()}: {value}")


@cli.command()
@click.option("--method", "-m", default="GET", help="HTTP method")
@click.option("--url", "-U", required=True, help="Target URL to proxy")
@click.option("--target", "-T", default="default", help="Target name from config")
@click.option(
    "--header",
    "-H",
    multiple=True,
    help="Headers in 'Key: Value' format (can be used multiple times)",
)
@click.option("--header-json", type=str, default=None, help="Headers as JSON object")
@click.option("--data-json", "-d", type=str, default=None, help="Request body as JSON string")
@click.option(
    "--data", "-D", type=click.Path(exists=True), default=None, help="Request body from file"
)
@click.option("--timeout", type=float, default=None, help="Request timeout (overrides global)")
@click.option("--retries", "-r", type=int, default=None, help="Number of retries")
@click.option("--idempotency-key", "-i", type=str, default=None, help="Idempotency key")
@click.option("--cache", "-c", type=int, default=None, help="Cache TTL in seconds")
@click.pass_context
def request(
    ctx: click.Context,
    method: str,
    url: str,
    target: str,
    header: tuple,
    header_json: Optional[str],
    data_json: Optional[str],
    data: Optional[str],
    timeout: Optional[float],
    retries: Optional[int],
    idempotency_key: Optional[str],
    cache: Optional[int],
) -> None:
    """Make a proxied HTTP request through ReliAPI.

    Examples:
        reli request --method GET --url https://api.example.com/users
        reli request -m POST -U https://api.example.com/users -d '{"name": "John"}'
        reli request -m GET -U https://httpbin.org/get -H "X-Custom: value"
    """
    client = get_client(ctx)
    output_format = ctx.obj["output"]

    # Build headers
    headers_dict: dict[str, str] = {}

    # Parse --header options
    for h in header:
        if ":" in h:
            key, value = h.split(":", 1)
            headers_dict[key.strip()] = value.strip()

    # Merge with --header-json
    if header_json:
        try:
            headers_dict.update(json.loads(header_json))
        except json.JSONDecodeError:
            console.print("[red]Error:[/red] Invalid JSON for --header-json")
            sys.exit(1)

    # Build body
    body = None
    if data_json:
        body = data_json
    elif data:
        with open(data, "r") as f:
            body = f.read()

    # Parse URL to extract path
    from urllib.parse import urlparse

    parsed = urlparse(url)
    path = parsed.path
    if parsed.query:
        path += f"?{parsed.query}"

    # Build request payload
    payload = {
        "target": target,
        "method": method.upper(),
        "path": path,
    }

    if headers_dict:
        payload["headers"] = headers_dict
    if body:
        payload["body"] = body
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    if cache is not None:
        payload["cache"] = cache

    try:
        result = client.post("/proxy/http", payload)

        if output_format == "json":
            console.print_json(json.dumps(result))
        elif output_format == "table":
            table = Table(title="Request Result")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="green")

            # Show success/error
            table.add_row("Success", str(result.get("success", False)))

            # Show meta info
            meta = result.get("meta", {})
            table.add_row("Request ID", meta.get("request_id", "N/A"))
            table.add_row("Duration (ms)", str(meta.get("duration_ms", 0)))
            table.add_row("Cache Hit", str(meta.get("cache_hit", False)))
            table.add_row("Retries", str(meta.get("retries", 0)))

            # Show data or error
            if result.get("success"):
                data_str = json.dumps(result.get("data", {}))
                if len(data_str) > 100:
                    data_str = data_str[:97] + "..."
                table.add_row("Data", data_str)
            else:
                error = result.get("error", {})
                table.add_row("Error Code", error.get("code", "N/A"))
                table.add_row("Error Message", error.get("message", "N/A"))

            console.print(table)
        else:
            if result.get("success"):
                console.print(
                    f"✓ Success (duration: {result.get('meta', {}).get('duration_ms', 0)}ms)"
                )
                console.print(json.dumps(result.get("data", {}), indent=2))
            else:
                error = result.get("error", {})
                console.print(f"✗ Error: {error.get('code', 'UNKNOWN')}")
                console.print(f"  Message: {error.get('message', 'No message')}")

    except httpx.HTTPError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--target", "-T", required=True, help="LLM target name (openai, anthropic, mistral)")
@click.option("--model", "-m", default=None, help="Model name (e.g., gpt-4o-mini)")
@click.option("--message", "-M", required=True, help="User message")
@click.option("--system", "-s", default=None, help="System prompt")
@click.option("--max-tokens", type=int, default=None, help="Max tokens in response")
@click.option("--temperature", type=float, default=None, help="Temperature (0.0-2.0)")
@click.option("--idempotency-key", "-i", type=str, default=None, help="Idempotency key")
@click.option("--cache", "-c", type=int, default=None, help="Cache TTL in seconds")
@click.pass_context
def llm(
    ctx: click.Context,
    target: str,
    model: Optional[str],
    message: str,
    system: Optional[str],
    max_tokens: Optional[int],
    temperature: Optional[float],
    idempotency_key: Optional[str],
    cache: Optional[int],
) -> None:
    """Make an LLM request through ReliAPI.

    Examples:
        reli llm --target openai --message "Hello, world!"
        reli llm -T anthropic -m claude-3-haiku -M "Explain quantum computing"
    """
    client = get_client(ctx)
    output_format = ctx.obj["output"]

    # Build messages
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": message})

    # Build request payload
    payload = {
        "target": target,
        "messages": messages,
    }

    if model:
        payload["model"] = model
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if temperature is not None:
        payload["temperature"] = temperature
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    if cache is not None:
        payload["cache"] = cache

    try:
        result = client.post("/proxy/llm", payload)

        if output_format == "json":
            console.print_json(json.dumps(result))
        elif output_format == "table":
            table = Table(title="LLM Response")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="green")

            table.add_row("Success", str(result.get("success", False)))

            meta = result.get("meta", {})
            table.add_row("Model", meta.get("model", "N/A"))
            table.add_row("Duration (ms)", str(meta.get("duration_ms", 0)))
            table.add_row("Cache Hit", str(meta.get("cache_hit", False)))

            if result.get("success"):
                data = result.get("data", {})
                content = data.get("content", "")
                if len(content) > 200:
                    content = content[:197] + "..."
                table.add_row("Response", content)

                usage = data.get("usage", {})
                if usage:
                    table.add_row(
                        "Tokens (in/out)",
                        f"{usage.get('prompt_tokens', 0)}/{usage.get('completion_tokens', 0)}",
                    )
                    if usage.get("estimated_cost_usd"):
                        table.add_row("Cost", f"${usage['estimated_cost_usd']:.6f}")
            else:
                error = result.get("error", {})
                table.add_row("Error", error.get("message", "N/A"))

            console.print(table)
        else:
            if result.get("success"):
                data = result.get("data", {})
                console.print(data.get("content", ""))
            else:
                error = result.get("error", {})
                console.print(f"[red]Error:[/red] {error.get('message', 'Unknown error')}")
                sys.exit(1)

    except httpx.HTTPError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def main() -> None:
    """Main entry point."""
    cli(obj={})


if __name__ == "__main__":
    main()
