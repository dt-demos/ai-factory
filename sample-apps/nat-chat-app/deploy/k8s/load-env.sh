# Sourced by the deploy scripts: loads ../../.env (sample-apps/nat-chat-app/.env) if present.
# Values in that file override variables already set in the shell.
ENV_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi
