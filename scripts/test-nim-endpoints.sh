#!/usr/bin/env bash
# Test NIM model endpoints (prompt + metrics) and DCGM GPU metrics from a
# throwaway curl pod inside the cluster, then print a summary table.
#
# Usage:
#   ./test-nim-endpoints.sh                       # all default services
#   ./test-nim-endpoints.sh nemotron-70b-instruct:/metrics   # just one, as service[:metrics_path]
#
# Metrics path differs by NIM build (default /v1/metrics if not given).
#
# Env overrides:
#   NIM_NAMESPACE  (default: nim-service)
#   NIM_PORT       (default: 8000)
#   DCGM_TARGET    (default: nvidia-dcgm-exporter.gpu-operator:9400, set to "" to skip)
#   PROMPT         (default: "In one sentence, what is observability?")

set -euo pipefail

NIM_NAMESPACE="${NIM_NAMESPACE:-nim-service}"
NIM_PORT="${NIM_PORT:-8000}"
DCGM_TARGET="${DCGM_TARGET-nvidia-dcgm-exporter.gpu-operator:9400}"
PROMPT="${PROMPT:-In one sentence, what is observability?}"

if [ "$#" -gt 0 ]; then
  SERVICES="$*"
else
  SERVICES="nemotron-70b-instruct:/metrics content-safety:/v1/metrics topic-control:/v1/metrics embedqa-e5-v5:/v1/metrics"
fi

# Runs inside the curlimages/curl pod (busybox sh, no jq)
read -r -d '' INNER <<'EOF' || true
NS="$1"; PORT="$2"; DCGM="$3"; PROMPT="$4"; shift 4

KEY_METRICS='num_requests_running|num_requests_waiting|gpu_cache_usage_perc|request_success_total|prompt_tokens_total|generation_tokens_total|time_to_first_token_seconds_(sum|count)|e2e_request_latency_seconds_(sum|count)'
METRICS_ROWS=""
MODEL_ROWS=""

# Print "a|b|c" rows as a table with columns sized to fit
table() {
  printf '%s' "$2" | awk -F'|' -v title="$1" '
    { for (i = 1; i <= NF; i++) { cell[NR, i] = $i; if (length($i) > w[i]) w[i] = length($i) } n = NF }
    END {
      print "== " title
      for (r = 1; r <= NR; r++) {
        line = ""
        for (i = 1; i <= n; i++) line = line sprintf("%-" w[i] + 2 "s", cell[r, i])
        print line
        if (r == 1) { sep = ""; for (i = 1; i <= n; i++) { d = ""; for (j = 0; j < w[i]; j++) d = d "-"; sep = sep sprintf("%-" w[i] + 2 "s", d) } print sep }
      }
      print ""
    }'
}

for ENTRY in "$@"; do
  SVC="${ENTRY%%:*}"
  MPATH="${ENTRY#*:}"
  [ "$MPATH" != "$ENTRY" ] || MPATH="/v1/metrics"
  BASE="http://$SVC.$NS:$PORT"
  echo "== $SVC  ($BASE)"

  MODEL=$(curl -s --max-time 10 "$BASE/v1/models" | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4)
  [ -n "$MODEL" ] || MODEL="$SVC (no /v1/models)"
  echo "model: $MODEL"

  # Prompt
  PROMPT_OK=no
  case "$SVC$MODEL" in
    *embed*|*e5*)
      MODEL_URL="$BASE/v1/embeddings"
      RESP=$(curl -s --max-time 60 "$MODEL_URL" -H "Content-Type: application/json" \
        -d "{\"model\":\"$MODEL\",\"input\":[\"$PROMPT\"],\"input_type\":\"query\"}")
      DIMS=$(echo "$RESP" | grep -o '"embedding":\[[^]]*\]' | head -1 | tr ',' '\n' | wc -l)
      if [ "$DIMS" -gt 1 ]; then
        PROMPT_OK=yes; echo "prompt: OK, embedding with $DIMS dimensions"
      else
        echo "prompt: FAIL $(echo "$RESP" | cut -c1-300)"
      fi
      ;;
    *)
      MODEL_URL="$BASE/v1/chat/completions"
      RESP=$(curl -s --max-time 120 "$MODEL_URL" -H "Content-Type: application/json" \
        -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"$PROMPT\"}],\"max_tokens\":100}")
      CONTENT=$(echo "$RESP" | grep -o '"content":"[^"]*"' | head -1 | cut -d'"' -f4)
      if [ -n "$CONTENT" ]; then
        PROMPT_OK=yes; echo "prompt: OK, $CONTENT"
        echo "        $(echo "$RESP" | grep -o '"usage":{[^}]*}')"
      else
        echo "prompt: FAIL $(echo "$RESP" | cut -c1-300)"
      fi
      ;;
  esac

  # Metrics
  METRICS_URL="$BASE$MPATH"
  METRICS_OK=no
  BODY=$(curl -s -f --max-time 10 "$METRICS_URL" || true)
  if [ -n "$BODY" ]; then
    METRICS_OK=yes
    echo "metrics: OK"
    KEY=$(echo "$BODY" | grep -v '^#' | grep -E "$KEY_METRICS" | grep -v '_bucket' | head -10)
    # Non-LLM NIMs (e.g. embedding) use other metric names, so fall back to a sample
    [ -n "$KEY" ] || KEY=$(echo "$BODY" | grep -v '^#' | grep -vE '^(python_|process_)' | grep -v '_bucket' | head -10)
    echo "$KEY" | sed 's/^/  /'
  else
    echo "metrics: FAIL ($METRICS_URL)"
  fi
  echo

  METRICS_ROWS="$METRICS_ROWS$MODEL|$METRICS_URL|$METRICS_OK
"
  MODEL_ROWS="$MODEL_ROWS$MODEL|$MODEL_URL|$PROMPT_OK
"
done

if [ -n "$DCGM" ]; then
  DCGM_URL="http://$DCGM/metrics"
  echo "== DCGM GPU metrics  ($DCGM_URL)"
  DCGM_OK=no
  BODY=$(curl -s -f --max-time 10 "$DCGM_URL" | grep -v '^#' | grep -E '^DCGM_FI_DEV_(GPU_UTIL|FB_USED|GPU_TEMP|POWER_USAGE)' || true)
  if [ -n "$BODY" ]; then
    DCGM_OK=yes
    echo "$BODY" | head -10 | sed 's/^/  /'
    echo "  (service load-balances across exporter pods, so this shows one node per run)"
  else
    echo "metrics: FAIL"
  fi
  echo
  METRICS_ROWS="$METRICS_ROWS""dcgm-exporter (GPU)|$DCGM_URL|$DCGM_OK
"
fi

table "SUMMARY: METRICS" "MODEL|METRICS ENDPOINT|WORKS
$METRICS_ROWS"
table "SUMMARY: MODEL ENDPOINTS" "MODEL|MODEL ENDPOINT|PROMPT WORKS
$MODEL_ROWS"
EOF

kubectl run "nim-test-$$" --rm -i --quiet --restart=Never --image=curlimages/curl --command -- \
  sh -c "$INNER" sh "$NIM_NAMESPACE" "$NIM_PORT" "$DCGM_TARGET" "$PROMPT" $SERVICES
