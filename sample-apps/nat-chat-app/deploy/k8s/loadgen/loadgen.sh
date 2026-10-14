#!/usr/bin/env bash
# Run the continuous prompt generator (loadgen.py) in the cluster. See "Continuous load" in
# ../README.md.
#
# Usage:
#   ./loadgen.sh start       # create/update the ConfigMap and Deployment, start sending prompts
#   ./loadgen.sh stop        # pause (scale to 0); `start` resumes
#   ./loadgen.sh status      # pod status, latest summary, any unexpected results
#   ./loadgen.sh logs        # follow the log (one JSON line per prompt)
#   ./loadgen.sh delete      # remove the Deployment and ConfigMap
#
# Settings: set LOADGEN_* variables when running start, for example
#   LOADGEN_DELAY_MIN=30 LOADGEN_DELAY_MAX=90 ./loadgen.sh start
#   LOADGEN_WEIGHTS="valid_with_guardrails=0,valid_without_guardrails=0" ./loadgen.sh start   # blocked prompts only
# IMAGE=<image> overrides the image (default: the image the app deployment is running).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../load-env.sh"
NAMESPACE=nim
DEPLOYMENT=nat-chat-loadgen
CONFIGMAP=nat-chat-loadgen

start() {
  local image="${IMAGE:-}"
  if [ -z "$image" ]; then
    image="$(kubectl -n "$NAMESPACE" get deploy nat-chat-app \
      -o jsonpath='{.spec.template.spec.containers[0].image}')"
  fi
  echo "image: $image"

  kubectl -n "$NAMESPACE" create configmap "$CONFIGMAP" \
    --from-file=loadgen.py="$SCRIPT_DIR/loadgen.py" \
    --from-file=prompts.yaml="$SCRIPT_DIR/prompts.yaml" \
    --dry-run=client -o yaml | kubectl apply -f -

  sed "s#__IMAGE__#${image}#" "$SCRIPT_DIR/loadgen.yaml" | kubectl apply -f -

  # Apply any LOADGEN_* settings given in the environment on top of the manifest defaults.
  local settings=()
  while IFS= read -r line; do settings+=("$line"); done < <(env | grep '^LOADGEN_' || true)
  if [ "${#settings[@]}" -gt 0 ]; then
    kubectl -n "$NAMESPACE" set env deploy/"$DEPLOYMENT" "${settings[@]}"
  fi

  # Pick up a changed ConfigMap (loadgen.py and prompts.yaml are read once at startup).
  kubectl -n "$NAMESPACE" rollout restart deploy/"$DEPLOYMENT"
  kubectl -n "$NAMESPACE" rollout status deploy/"$DEPLOYMENT" --timeout=180s
}

stop() {
  kubectl -n "$NAMESPACE" scale deploy/"$DEPLOYMENT" --replicas=0
}

status() {
  kubectl -n "$NAMESPACE" get deploy,pods -l app="$DEPLOYMENT"
  local log
  log="$(kubectl -n "$NAMESPACE" logs deploy/"$DEPLOYMENT" --tail=3000 2>&1 || true)"
  echo "--- latest summary"
  printf '%s\n' "$log" | grep -A12 -- '--- summary' | tail -13 || echo "(no summary yet, one prints every 10 prompts)"
  echo "--- unexpected results (last 10)"
  printf '%s\n' "$log" | grep '"match": false' | tail -10 || true
  echo "--- last prompt"
  printf '%s\n' "$log" | grep '^{' | tail -1 || true
}

logs() {
  kubectl -n "$NAMESPACE" logs -f deploy/"$DEPLOYMENT" --tail=50
}

delete() {
  kubectl -n "$NAMESPACE" delete deploy "$DEPLOYMENT" --ignore-not-found
  kubectl -n "$NAMESPACE" delete configmap "$CONFIGMAP" --ignore-not-found
}

case "${1:-}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  logs)   logs ;;
  delete) delete ;;
  *) echo "Usage: $0 {start|stop|status|logs|delete}" >&2; exit 1 ;;
esac
