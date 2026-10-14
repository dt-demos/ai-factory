#!/usr/bin/env bash
# Run smoke.py inside the running nat-chat-app pod (see "Smoke test" in README.md).
#
# Usage:
#   ./smoke.sh                                      # default question, with and without guardrails
#   ./smoke.sh "What is Dynatrace?" --mode with     # arguments are passed on to smoke.py
#
# The exit code is smoke.py's: non-zero if a mode failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE=nim

kubectl -n "$NAMESPACE" exec -i deploy/nat-chat-app -c app -- python - "$@" < "$SCRIPT_DIR/smoke.py"
