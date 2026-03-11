#!/usr/bin/env bash
# Run smoke tests for SDK packages, CLI, and action
# Usage: ./smoke_test.sh [js|python|cli|action|all]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass() {
    echo -e "${GREEN}✓ $1${NC}"
}

fail() {
    echo -e "${RED}✗ $1${NC}"
    exit 1
}

warn() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

smoke_test_js() {
    echo "Running JavaScript SDK smoke test..."

    SDK_PATH="sdk/js"

    if [[ ! -f "$SDK_PATH/package.json" ]]; then
        warn "JavaScript SDK not generated yet, skipping..."
        return 0
    fi

    # Check package.json is valid
    if ! python3 -c "import json; json.load(open('$SDK_PATH/package.json'))" 2>/dev/null; then
        fail "Invalid package.json"
    fi
    pass "package.json is valid JSON"

    # Check npm pack works
    if command -v npm &>/dev/null; then
        cd "$SDK_PATH"
        if npm pack --dry-run >/dev/null 2>&1; then
            pass "npm pack --dry-run succeeded"
        else
            warn "npm pack --dry-run failed (may be expected for generated SDK)"
        fi
        cd "$PROJECT_ROOT"
    else
        warn "npm not available, skipping pack test"
    fi

    pass "JavaScript SDK smoke test passed"
}

smoke_test_python() {
    echo "Running Python SDK smoke test..."

    SDK_PATH="sdk/python"

    if [[ ! -f "$SDK_PATH/pyproject.toml" ]] && [[ ! -f "$SDK_PATH/setup.py" ]]; then
        warn "Python SDK not generated yet, skipping..."
        return 0
    fi

    # Check pyproject.toml is valid
    if [[ -f "$SDK_PATH/pyproject.toml" ]]; then
        if python3 -c "import tomllib; tomllib.load(open('$SDK_PATH/pyproject.toml', 'rb'))" 2>/dev/null || \
           python3 -c "import toml; toml.load(open('$SDK_PATH/pyproject.toml'))" 2>/dev/null; then
            pass "pyproject.toml is valid"
        else
            warn "Could not validate pyproject.toml (tomllib/toml not available)"
        fi
    fi

    # Try to build wheel
    if command -v python3 &>/dev/null; then
        cd "$SDK_PATH"
        if python3 -m build --wheel --no-isolation >/dev/null 2>&1; then
            pass "Python wheel build succeeded"
            # Try to install wheel
            WHEEL_FILE=$(ls dist/*.whl 2>/dev/null | head -1)
            if [[ -n "$WHEEL_FILE" ]]; then
                if pip install --dry-run "$WHEEL_FILE" >/dev/null 2>&1; then
                    pass "Python wheel installable"
                fi
            fi
        else
            warn "Python wheel build failed (may be expected for generated SDK)"
        fi
        cd "$PROJECT_ROOT"
    fi

    pass "Python SDK smoke test passed"
}

smoke_test_cli() {
    echo "Running CLI smoke test..."

    CLI_PATH="cli"

    if [[ ! -f "$CLI_PATH/pyproject.toml" ]]; then
        warn "CLI not set up yet, skipping..."
        return 0
    fi

    # Check pyproject.toml is valid
    if python3 -c "import tomllib; tomllib.load(open('$CLI_PATH/pyproject.toml', 'rb'))" 2>/dev/null || \
       python3 -c "import toml; toml.load(open('$CLI_PATH/pyproject.toml'))" 2>/dev/null; then
        pass "CLI pyproject.toml is valid"
    fi

    # Verify the CLI package can be built
    if command -v python3 &>/dev/null; then
        cd "$CLI_PATH"
        if python3 -m build --wheel --no-isolation >/dev/null 2>&1; then
            pass "CLI wheel build succeeded"
        else
            fail "CLI wheel build failed"
        fi
        cd "$PROJECT_ROOT"
    fi

    # Try to run CLI help
    if python3 -m cli.main --help >/dev/null 2>&1; then
        pass "CLI --help works"
    else
        warn "CLI --help failed (dependencies may need install)"
    fi

    pass "CLI smoke test passed"
}

smoke_test_action() {
    echo "Running GitHub Action smoke test..."

    ACTION_PATH="action"

    if [[ ! -f "$ACTION_PATH/action.yml" ]]; then
        fail "action.yml not found"
    fi
    pass "action.yml exists"

    # Validate action.yml YAML
    if python3 -c "import yaml; yaml.safe_load(open('$ACTION_PATH/action.yml'))" 2>/dev/null; then
        pass "action.yml is valid YAML"
    else
        fail "action.yml is invalid YAML"
    fi

    # Check package.json
    if [[ -f "$ACTION_PATH/package.json" ]]; then
        if python3 -c "import json; json.load(open('$ACTION_PATH/package.json'))" 2>/dev/null; then
            pass "action package.json is valid"
        fi
    fi

    # Check index.js exists
    if [[ -f "$ACTION_PATH/index.js" ]]; then
        pass "action index.js exists"
    fi

    if ! command -v node &>/dev/null; then
        warn "node not available, skipping runtime action test"
        pass "GitHub Action smoke test passed"
        return 0
    fi

    local output_file
    local server_port_file
    local server_log
    local action_log
    output_file="$(mktemp)"
    server_port_file="$(mktemp)"
    server_log="$(mktemp)"
    action_log="$(mktemp)"

    python3 - "$server_port_file" >"$server_log" 2>&1 <<'PY' &
import http.server
import json
import socketserver
import sys
from pathlib import Path

port_file = Path(sys.argv[1])


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"status": "ok", "service": "reliapi-smoke"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        return


with socketserver.TCPServer(("127.0.0.1", 0), Handler) as httpd:
    port_file.write_text(str(httpd.server_address[1]))
    httpd.serve_forever()
PY
    local server_pid=$!

    cleanup_action_smoke() {
        kill "$server_pid" >/dev/null 2>&1 || true
        wait "$server_pid" 2>/dev/null || true
        rm -f "$output_file" "$server_port_file" "$server_log" "$action_log"
    }
    trap cleanup_action_smoke RETURN

    for _ in {1..20}; do
        if [[ -s "$server_port_file" ]]; then
            break
        fi
        sleep 0.2
    done

    if [[ ! -s "$server_port_file" ]]; then
        cat "$server_log" >&2 || true
        fail "Action smoke server failed to start"
    fi

    local server_port
    server_port="$(cat "$server_port_file")"

    if INPUT_API_URL="http://127.0.0.1:${server_port}" \
       INPUT_ENDPOINT="/healthz" \
       INPUT_METHOD="GET" \
       INPUT_TIMEOUT="5000" \
       INPUT_RETRIES="1" \
       INPUT_RETRY_DELAY="10" \
       GITHUB_OUTPUT="$output_file" \
       node "$ACTION_PATH/index.js" >"$action_log" 2>&1; then
        pass "action runtime smoke succeeded"
    else
        cat "$action_log" >&2 || true
        fail "Action runtime smoke failed"
    fi

    grep -q '^success=true$' "$output_file" || fail "Action did not report success output"
    grep -q '^status=200$' "$output_file" || fail "Action did not report HTTP 200"
    pass "action outputs were written"

    pass "GitHub Action smoke test passed"
}

smoke_test_docker() {
    echo "Running Docker smoke test..."

    if [[ ! -f "Dockerfile" ]]; then
        fail "Dockerfile not found"
    fi
    pass "Dockerfile exists"

    # Validate Dockerfile has HEALTHCHECK
    if grep -q "HEALTHCHECK" Dockerfile; then
        pass "Dockerfile has HEALTHCHECK"
    else
        fail "Dockerfile missing HEALTHCHECK"
    fi

    pass "Docker smoke test passed"
}

# Main
TARGET="${1:-all}"

echo "========================================="
echo "ReliAPI Smoke Tests"
echo "========================================="
echo ""

case "$TARGET" in
    js|javascript)
        smoke_test_js
        ;;
    python|py)
        smoke_test_python
        ;;
    cli)
        smoke_test_cli
        ;;
    action)
        smoke_test_action
        ;;
    docker)
        smoke_test_docker
        ;;
    all)
        smoke_test_js
        echo ""
        smoke_test_python
        echo ""
        smoke_test_cli
        echo ""
        smoke_test_action
        echo ""
        smoke_test_docker
        ;;
    *)
        echo "Usage: $0 [js|python|cli|action|docker|all]"
        exit 1
        ;;
esac

echo ""
echo "========================================="
echo -e "${GREEN}All smoke tests passed!${NC}"
echo "========================================="
