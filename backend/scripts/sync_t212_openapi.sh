#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/openapi/trading212-api.json"

curl -fsSL https://docs.trading212.com/_bundle/api.json -o "$TARGET"
echo "Saved OpenAPI spec to $TARGET"
