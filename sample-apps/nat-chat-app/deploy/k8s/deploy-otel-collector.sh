#!/usr/bin/env bash
# Deploy the Dynatrace OTel Collector into the cluster using the raw-manifest
# approach, with otel/config.yaml as the collector config.
#
# Usage:
#   export DT_BASE_URL=https://<tenant>.live.dynatrace.com
#   export DT_API_TOKEN=<token with openTelemetryTrace.ingest, metrics.ingest, logs.ingest>
#   ENRICHED=1 ./deploy-otel-collector.sh apply   # use config-enriched.yaml
#   ./deploy-otel-collector.sh apply    # create/update secret, configmap, deployment
#   ./deploy-otel-collector.sh status   # pod status + recent logs
#   ./deploy-otel-collector.sh delete   # remove everything

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE=nim
# Default: plain config. Set ENRICHED=1 to use otel/config-enriched.yaml
# (adds k8sattributes + transform for Kubernetes enrichment).
if [ "${ENRICHED:-0}" = "1" ]; then
  CONFIG_FILE="$SCRIPT_DIR/otel/config-enriched.yaml"
else
  CONFIG_FILE="$SCRIPT_DIR/otel/config.yaml"
fi
SECRET=dynatrace-otelcol-dt-api-credentials
CONFIGMAP=dynatrace-otel-collector-config
DEPLOYMENT=dynatrace-otel-collector

apply() {
  : "${DT_BASE_URL:?DT_BASE_URL not set}"
  : "${DT_API_TOKEN:?DT_API_TOKEN not set}"

  kubectl get namespace "$NAMESPACE" >/dev/null 2>&1 || kubectl create namespace "$NAMESPACE"

  kubectl -n "$NAMESPACE" create secret generic "$SECRET" \
    --from-literal=DT_BASE_URL="${DT_BASE_URL%/}" \
    --from-literal=DT_API_TOKEN="$DT_API_TOKEN" \
    --dry-run=client -o yaml | kubectl apply -f -

  kubectl -n "$NAMESPACE" create configmap "$CONFIGMAP" \
    --from-file=otel-collector-config="$CONFIG_FILE" \
    --dry-run=client -o yaml | kubectl apply -f -

  kubectl apply -f "$SCRIPT_DIR/otel/otel-collector.yaml"

  # Pick up config/secret changes on re-apply
  kubectl -n "$NAMESPACE" rollout restart deployment "$DEPLOYMENT"
  kubectl -n "$NAMESPACE" rollout status deployment "$DEPLOYMENT" --timeout=120s
}

status() {
  kubectl -n "$NAMESPACE" get deploy,po,svc -l app.kubernetes.io/name=dynatrace-otel-collector
  kubectl -n "$NAMESPACE" get svc dynatrace-otel-collector
  echo "--- recent errors/warnings"
  kubectl -n "$NAMESPACE" logs deploy/"$DEPLOYMENT" --tail=500 | grep -iE '"level":"(error|warn)"|error|warn' | tail -20 || echo "(none)"
}

delete() {
  kubectl delete -f "$SCRIPT_DIR/otel/otel-collector.yaml" --ignore-not-found
  kubectl -n "$NAMESPACE" delete configmap "$CONFIGMAP" --ignore-not-found
  kubectl -n "$NAMESPACE" delete secret "$SECRET" --ignore-not-found
}

case "${1:-}" in
  apply)  apply ;;
  status) status ;;
  delete) delete ;;
  *) echo "Usage: $0 {apply|status|delete}"; exit 1 ;;
esac
