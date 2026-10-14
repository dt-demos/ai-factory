#!/usr/bin/env bash
# Run quick-test-app.py inside the running nat-chat-app pod (see "Quick test" in README.md).
#
# Usage:
#   ./quick-test-app.sh                                     # default question, with and without guardrails
#   ./quick-test-app.sh "What is Dynatrace?" --mode with    # arguments are passed on to quick-test-app.py
#
# The exit code is quick-test-app.py's: non-zero if a mode failed.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE=nim

kubectl -n "$NAMESPACE" exec -i deploy/nat-chat-app -c app -- python - "$@" < "$SCRIPT_DIR/quick-test-app.py"
