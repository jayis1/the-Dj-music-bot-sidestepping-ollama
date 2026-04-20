#!/bin/bash
# ────────────────────────────────────────────────────────────────────
# Shadow Controller — Quick Run Script
# Activates venv and starts the controller
# ────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check config exists
if [ ! -f "config.yaml" ]; then
    echo "❌ No config.yaml found. Run setup.sh first, or copy config.example.yaml → config.yaml"
    exit 1
fi

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "⚠  No venv found. Run setup.sh first."
    exit 1
fi

# Run the shadow controller as a module
echo "Starting Shadow Controller..."
echo "   Press Ctrl+C to stop"
echo ""

python -m shadow_controller "$@"