#!/usr/bin/env bash
# Deploy the nat-chat-app into the cluster. The Tavily API key is read from the
# environment and stored in the app-secrets Secret (it is not kept in app.yaml).
#
# Usage:
#   Set TAVILY_API_KEY in sample-apps/nat-chat-app/.env (or export it)
#   ./deploy-app.sh apply    # create/update secret, then apply app.yaml
#   ./deploy-app.sh status   # pod status + recent logs
#   ./deploy-app.sh delete   # remove everything

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/load-env.sh"
NAMESPACE=nim
SECRET=app-secrets
DEPLOYMENT=nat-chat-app

apply() {
  : "${TAVILY_API_KEY:?TAVILY_API_KEY not set}"

  kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

  kubectl -n "$NAMESPACE" create secret generic "$SECRET" \
    --from-literal=TAVILY_API_KEY="$TAVILY_API_KEY" \
    --dry-run=client -o yaml | kubectl apply -f -

  kubectl apply -f "$SCRIPT_DIR/app.yaml"

  # Pick up config/secret changes on re-apply
  kubectl -n "$NAMESPACE" rollout restart deployment "$DEPLOYMENT"
  kubectl -n "$NAMESPACE" rollout status deployment "$DEPLOYMENT" --timeout=300s
}

status() {
  kubectl -n "$NAMESPACE" get deploy,po,svc -l app=nat-chat-app
  echo "--- recent errors/warnings"
  kubectl -n "$NAMESPACE" logs deploy/"$DEPLOYMENT" --tail=500 | grep -iE 'error|warn' | tail -20 || echo "(none)"
}

delete() {
  kubectl delete -f "$SCRIPT_DIR/app.yaml" --ignore-not-found
  kubectl -n "$NAMESPACE" delete secret "$SECRET" --ignore-not-found
}

case "${1:-}" in
  apply)  apply ;;
  status) status ;;
  delete) delete ;;
  *) echo "Usage: $0 {apply|status|delete}"; exit 1 ;;
esac
