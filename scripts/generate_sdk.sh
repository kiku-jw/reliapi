#!/usr/bin/env bash
# Generate SDK from OpenAPI specification
# Usage: ./generate_sdk.sh [js|python|all]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Parse product.yaml for configuration
get_config() {
    python3 -c "import yaml; c=yaml.safe_load(open('product.yaml')); print($1)"
}

PRODUCT_NAME=$(get_config "c['product']['name']")
PRODUCT_VERSION=$(get_config "c['product']['version']")
NPM_PACKAGE=$(get_config "c['packages']['npm']['name']")
PYPI_PACKAGE=$(get_config "c['packages']['pypi']['name']")
OPENAPI_PATH=$(get_config "c['openapi']['output']")

SDK_JS_PATH="sdk/js"
SDK_PYTHON_PATH="sdk/python"

java_major_version() {
    if ! command -v java &>/dev/null; then
        return 1
    fi

    java -version 2>&1 | awk -F[\".] '/version/ {
        if ($2 == "1") {
            print $3
        } else {
            print $2
        }
        exit
    }'
}

supports_npx_generator() {
    local major=""
    major=$(java_major_version || true)
    [[ -n "$major" && "$major" =~ ^[0-9]+$ && "$major" -ge 11 ]]
}

select_generator() {
    local __generator_var="$1"
    local __openapi_var="$2"
    local __output_var="$3"
    local output_path="$4"
    local major=""

    if command -v npx &>/dev/null && supports_npx_generator; then
        printf -v "$__generator_var" "%s" "npx @openapitools/openapi-generator-cli"
        printf -v "$__openapi_var" "%s" "$OPENAPI_PATH"
        printf -v "$__output_var" "%s" "$output_path"
        return 0
    fi

    if command -v docker &>/dev/null; then
        printf -v "$__generator_var" "%s" \
            "docker run --rm -v ${PROJECT_ROOT}:/local openapitools/openapi-generator-cli"
        printf -v "$__openapi_var" "%s" "/local/$OPENAPI_PATH"
        printf -v "$__output_var" "%s" "/local/$output_path"
        return 0
    fi

    major=$(java_major_version || true)
    if command -v npx &>/dev/null; then
        if [[ -n "$major" ]]; then
            echo "Error: openapi-generator-cli via npx requires Java 11+ (found Java $major)." >&2
        else
            echo "Error: openapi-generator-cli via npx requires Java 11+, but java is not available." >&2
        fi
        echo "Install Java 11+ or use Docker for SDK generation." >&2
    else
        echo "Error: Neither npx with Java 11+ nor Docker is available for SDK generation." >&2
    fi
    exit 1
}

clean_sdk_dir() {
    local dir="$1"
    local keep_file="$2"

    mkdir -p "$dir"
    find "$dir" -mindepth 1 -maxdepth 1 ! -name "$keep_file" -exec rm -rf {} +
}

# Check if OpenAPI spec exists
if [[ ! -f "$OPENAPI_PATH" ]]; then
    echo "Error: OpenAPI spec not found at $OPENAPI_PATH"
    echo "Run 'make openapi' first"
    exit 1
fi

generate_js_sdk() {
    echo "Generating JavaScript/TypeScript SDK..."
    local generator=""
    local openapi_input="$OPENAPI_PATH"
    local sdk_js_output="$SDK_JS_PATH"

    select_generator generator openapi_input sdk_js_output "$SDK_JS_PATH"

    # Clean previous generation
    clean_sdk_dir "$SDK_JS_PATH" "package.json.tmpl"

    # Generate TypeScript SDK
    $generator generate \
        -i "$openapi_input" \
        -g typescript-fetch \
        -o "$sdk_js_output" \
        --additional-properties=npmName="$NPM_PACKAGE",npmVersion="$PRODUCT_VERSION",supportsES6=true,typescriptThreePlus=true

    # Create/update package.json from template if exists
    if [[ -f "$SDK_JS_PATH/package.json.tmpl" ]]; then
        sed -e "s/{{PRODUCT_NAME}}/$PRODUCT_NAME/g" \
            -e "s/{{PRODUCT_VERSION}}/$PRODUCT_VERSION/g" \
            -e "s|{{NPM_PACKAGE}}|$NPM_PACKAGE|g" \
            "$SDK_JS_PATH/package.json.tmpl" > "$SDK_JS_PATH/package.json.new"
        # Merge with generated package.json if needed
        mv "$SDK_JS_PATH/package.json.new" "$SDK_JS_PATH/package.json"
    fi

    # Install dependencies and build
    if [[ -f "$SDK_JS_PATH/package.json" ]] && command -v npm &>/dev/null; then
        cd "$SDK_JS_PATH"
        npm install 2>/dev/null || true
        npm run build 2>/dev/null || true
        cd "$PROJECT_ROOT"
    fi

    echo "JavaScript SDK generated at $SDK_JS_PATH"
}

generate_python_sdk() {
    echo "Generating Python SDK..."
    local generator=""
    local openapi_input="$OPENAPI_PATH"
    local sdk_python_output="$SDK_PYTHON_PATH"
    local package_name=""

    select_generator generator openapi_input sdk_python_output "$SDK_PYTHON_PATH"

    # Clean previous generation
    clean_sdk_dir "$SDK_PYTHON_PATH" "pyproject.toml.tmpl"

    # Generate Python SDK
    package_name=$(echo "$PYPI_PACKAGE" | tr '-' '_')
    $generator generate \
        -i "$openapi_input" \
        -g python \
        -o "$sdk_python_output" \
        --additional-properties=packageName="$package_name",packageVersion="$PRODUCT_VERSION",projectName="$PYPI_PACKAGE"

    # Create/update pyproject.toml from template if exists
    if [[ -f "$SDK_PYTHON_PATH/pyproject.toml.tmpl" ]]; then
        sed -e "s/{{PRODUCT_NAME}}/$PRODUCT_NAME/g" \
            -e "s/{{PRODUCT_VERSION}}/$PRODUCT_VERSION/g" \
            -e "s/{{PYPI_PACKAGE}}/$PYPI_PACKAGE/g" \
            "$SDK_PYTHON_PATH/pyproject.toml.tmpl" > "$SDK_PYTHON_PATH/pyproject.toml"
    fi

    if [[ -f LICENSE ]]; then
        cp LICENSE "$SDK_PYTHON_PATH/LICENSE"
    fi

    echo "Python SDK generated at $SDK_PYTHON_PATH"
}

# Main
TARGET="${1:-all}"

case "$TARGET" in
    js|javascript|ts|typescript)
        generate_js_sdk
        ;;
    python|py)
        generate_python_sdk
        ;;
    all)
        generate_js_sdk
        generate_python_sdk
        ;;
    *)
        echo "Usage: $0 [js|python|all]"
        exit 1
        ;;
esac

echo "SDK generation complete!"
