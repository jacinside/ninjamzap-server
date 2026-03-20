#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Deploying to Fly.io..."
cd "$SCRIPT_DIR"
fly deploy "$@"

echo "Done!"
