#!/usr/bin/env bash
# Generate Postman collection from OpenAPI specification
# Usage: ./generate_postman.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Parse product.yaml for configuration
get_config() {
    python3 -c "import yaml; c=yaml.safe_load(open('product.yaml')); print($1)"
}

PRODUCT_NAME=$(get_config "c['product']['name']")
DISPLAY_NAME=$(get_config "c['product']['display_name']")
OPENAPI_PATH=$(get_config "c['openapi']['output']")
POSTMAN_OUTPUT=$(get_config "c['postman']['output']")

# Check if OpenAPI spec exists
if [[ ! -f "$OPENAPI_PATH" ]]; then
    echo "Error: OpenAPI spec not found at $OPENAPI_PATH"
    echo "Run 'make openapi' first"
    exit 1
fi

echo "Generating Postman collection from $OPENAPI_PATH..."

# Create output directory
mkdir -p "$(dirname "$POSTMAN_OUTPUT")"

# Use openapi-to-postmanv2 via npx
if command -v npx &>/dev/null; then
    npx openapi-to-postmanv2 \
        -s "$OPENAPI_PATH" \
        -o "$POSTMAN_OUTPUT" \
        -p \
        --pretty

    # Update collection name if needed
    if command -v jq &>/dev/null && [[ -f "$POSTMAN_OUTPUT" ]]; then
        TMP_FILE=$(mktemp)
        jq ".info.name = \"$DISPLAY_NAME API\"" "$POSTMAN_OUTPUT" > "$TMP_FILE"
        mv "$TMP_FILE" "$POSTMAN_OUTPUT"
    fi

    echo "Postman collection generated: $POSTMAN_OUTPUT"
else
    echo "Error: npx not available. Install Node.js to use openapi-to-postmanv2"
    echo "Alternatively, you can convert manually at https://www.postman.com/tools/openapi-to-postman-converter/"
    exit 1
fi

echo "Postman collection generation complete!"

