# Run in Kubernetes (k8s profile)

The app runs as a pod and calls NIM services in the same cluster (`CONFIG_TYPE=k8s`). The NIM models run in the `nim-service` namespace. This folder deploys the OTel Collector and the chat app into the `nim` namespace.

| File | Purpose |
|---|---|
| `test-nim-endpoints.sh` | Checks every NIM model (prompt and metrics) and the DCGM GPU metrics from inside the cluster |
| `otel/otel-collector.yaml` | Dynatrace OTel Collector ServiceAccount, RBAC, Deployment and Service ([raw manifest approach](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector/deployment#kubernetes--raw-manifest)) |
| `otel/config.yaml` | Collector config: OTLP receiver, plus Prometheus scraping of the NIMs and DCGM |
| `deploy-otel-collector.sh` | Creates the Dynatrace secret and the collector ConfigMap (from `otel/config.yaml`), then applies `otel/otel-collector.yaml` |
| `app.yaml` | Chat app: Secret, ConfigMap, Deployment and LoadBalancer Service |

## 1. Check the NIM models

```bash
./test-nim-endpoints.sh
```

All models should show `yes` in both summary tables. The script runs a temporary curl pod, so you need kubectl access to the cluster.

| Model | Model endpoint | Metrics path |
|---|---|---|
| `nvidia/llama-3.1-nemotron-70b-instruct` | `nemotron-70b-instruct.nim-service:8000/v1` | `/metrics` |
| `nvidia/llama-3.1-nemoguard-8b-content-safety` | `content-safety.nim-service:8000/v1` | `/v1/metrics` |
| `nvidia/llama-3.1-nemoguard-8b-topic-control` | `topic-control.nim-service:8000/v1` | `/v1/metrics` |
| `nvidia/nv-embedqa-e5-v5` | `embedqa-e5-v5.nim-service:8000/v1` | `/v1/metrics` |

If your services differ, update the k8s templates (`../../guardrails_config/config.yml.k8s`, `../../src/nat_simple_web_query/configs/config.yml.k8s`) and `otel/config.yaml`.

## 2. Deploy the OTel Collector

The collector receives OTLP from the app on `dynatrace-otel-collector.nim:4317/4318`. It also scrapes these Prometheus endpoints:

| Job | Target | Path |
|---|---|---|
| dcgm | every `nvidia-dcgm-exporter` pod in `gpu-operator`, found via EndpointSlice discovery (one pod per GPU node) | `/metrics` |
| llama-3.1-nemotron-70b-instruct | `nemotron-70b-instruct.nim-service:8000` | `/metrics` |
| llama-3.1-nemoguard-8b-content-safety | `content-safety.nim-service:8000` | `/v1/metrics` |
| llama-3.1-nemoguard-8b-topic-control | `topic-control.nim-service:8000` | `/v1/metrics` |
| nv-embedqa-e5-v5 | `embedqa-e5-v5.nim-service:8000` | `/v1/metrics` |

```bash
export DT_BASE_URL=https://<tenant>.live.dynatrace.com
export DT_API_TOKEN=<token>   # scopes: openTelemetryTrace.ingest, metrics.ingest, logs.ingest

./deploy-otel-collector.sh apply    # also creates the nim namespace if needed
./deploy-otel-collector.sh status
```

After you edit `otel/config.yaml`, run `apply` again. It updates the ConfigMap and restarts the collector.

Check that metrics reach Dynatrace with these DQL queries:

```
timeseries avg(num_requests_running), by: { model_name }
timeseries avg(DCGM_FI_DEV_GPU_UTIL), by: { Hostname, gpu }
```

## 3. Publish the app image

Follow [Building and pushing the image](../../README.md#building-and-pushing-the-image) in the app README. Then set the new tag in `image:` in `app.yaml`. It's currently `docker.io/dtdemos/nat-chat-app:0.1`.

## 4. Set the configuration

Edit `app.yaml` before you apply it, and don't commit real keys:

| Setting | Where | Value |
|---|---|---|
| `TAVILY_API_KEY` | Secret `app-secrets` | Your Tavily key |
| `CONFIG_TYPE` | ConfigMap `app-config` | `k8s` |
| `OTEL_OTLP_ENDPOINT` | ConfigMap `app-config` | `http://dynatrace-otel-collector.nim.svc.cluster.local:4318` |
| `SERVICE_NAME_SUFFIX` | ConfigMap `app-config` | Optional. Appended to the service name (`nat-chat-app-<suffix>`) if desired |

## 5. Deploy the app

```bash
kubectl apply -f app.yaml
kubectl -n nim rollout status deployment nat-chat-app
kubectl -n nim logs deploy/nat-chat-app | head -20   # look for "Generating config for type: k8s"
```

After you change the ConfigMap or Secret, restart the app with `kubectl -n nim rollout restart deployment nat-chat-app`.

## 6. Open the app

```bash
kubectl -n nim get svc nat-chat-app
```

If `EXTERNAL-IP` stays `<pending>` (no load balancer in the cluster), port-forward instead and open `http://localhost:8501`:

```bash
kubectl -n nim port-forward svc/nat-chat-app 8501:80
```

## 7. Smoke test

[`smoke.py`](smoke.py) asks one question with and without NeMo Guardrails and exits non-zero if either run fails. Run it from a machine with `kubectl` access:

```bash
./smoke.sh
```

[`smoke.sh`](smoke.sh) is a wrapper for this command (copy `smoke.py` to another machine and run the command there if you prefer):

```bash
kubectl -n nim exec -i deploy/nat-chat-app -c app -- python - < smoke.py
```

Arguments go through to `smoke.py`. For example, one mode with your own question:

```bash
./smoke.sh "What is Dynatrace?" --mode with
```

`--mode` is `with`, `without` or `both` (the default).

**How it works.** `kubectl exec` starts a new `python` process inside the app's running pod, and `-` makes Python read the script from stdin, so the file on your machine is sent to the pod and nothing is built or copied into the image. The script uses Streamlit's `AppTest` to load `/app/app.py` from the pod's working directory and fill in the page headlessly (no browser, no web server). That means:

* It tests whatever image the pod is running. After you change the code, build and push a new image and roll it out first.
* It runs the real app code with the pod's environment: the ConfigMap and Secret, the NIM services and the collector.
* It is a separate process from the Streamlit server, so it starts its own tracing, guardrails and NAT workflow, and its logs and traces are not mixed with a browser session's. The traces still use the same service name, `nat-chat-app`.

**Finding the traces.** Each run prints a `TEST_RUN_ID`, which the app adds to the traces as `app.test.run.id`. Use it to find a run by more than its timestamp:

```
fetch spans, from:now()-1h
| filter app.test.run.id == "<TEST_RUN_ID>"
| fields trace.id, app.request.id, app.guardrails.enabled, start_time, duration
```

Every prompt also gets its own `app.request.id` (including prompts typed in the browser), and the pod name is on the trace as `k8s.pod.name`. To see a whole trace, filter on its `trace.id`.

Expect two traces per run of `smoke.py` (one per mode), each with a single request root (the `prompt` span).

## 8. Continuous load

[`loadgen/`](loadgen/) keeps a steady stream of prompts going, a mix of questions that should be answered and questions the guardrails should block. It is useful for filling dashboards and for checking the guardrails over time.

```bash
cd loadgen
./loadgen.sh start      # create the ConfigMap and Deployment, begin sending prompts
./loadgen.sh status     # pod, latest summary, any unexpected results
./loadgen.sh logs       # follow the log, one JSON line per prompt
./loadgen.sh stop       # pause; `start` resumes
./loadgen.sh delete     # remove it
```

**How it works.** It runs as its own pod (`nat-chat-loadgen`) from the same image the app is running, with the app's ConfigMap and Secret. Instead of Streamlit it runs [`loadgen.py`](loadgen/loadgen.py), which drives `app.py` headlessly like `smoke.py` does and submits one prompt at a time after a random delay. The script and [`prompts.yaml`](loadgen/prompts.yaml) are mounted from a ConfigMap, so changing the prompts or settings doesn't need a new image: edit the file and run `./loadgen.sh start` again.

**The mix.** The prompts come from [PROMPTS.md](../../PROMPTS.md). By default about 45% should be blocked (spread over jailbreak, blocked terms, politics, input length, LLM content safety and topic control), 35% are valid with guardrails and 20% are valid without. Each prompt has an expected outcome. The generator works out what the app actually did and logs `"match": false` when it differs, so `./loadgen.sh status` also shows where the guardrails or the document disagree with the code. `./loadgen.sh start` is also how you change settings, and they reset to the defaults unless you pass them again:

```bash
LOADGEN_DELAY_MIN=30 LOADGEN_DELAY_MAX=90 ./loadgen.sh start                          # faster
LOADGEN_WEIGHTS="valid_with_guardrails=0,valid_without_guardrails=0" ./loadgen.sh start  # blocked prompts only, no web searches
```

Before starting you can see the plan and the estimated rate on your own machine with `python3 loadgen/loadgen.py --list` (needs PyYAML and Streamlit).

**Cost.** At the default delay of 60-180 s, about 28 prompts an hour are sent. Every prompt that should be answered makes one web search (Tavily, about 15 an hour, 365 a day) and two 70B calls, each taking 15-25 s. Check your Tavily plan, and expect real users to share the GPU. Blocked prompts are nearly free.

**Finding the traffic in Dynatrace.** Every generated prompt has `app.test.run.id = "loadgen:<category>"` and `app.test.category = "<category>"`, and the log line has the `request_id` and `trace_id`. The app records what happened to every prompt (generated or not) on the `prompt` span as `app.outcome` (`passed`, `blocked_input`, `blocked_output`, `workflow_error` or `guardrail_error`) and, when a guardrail stopped it, `app.blocked_by` (for example `Politics Filter`):

```
fetch spans, from:now()-1h
| filter startsWith(app.test.run.id, "loadgen:") and span.name == "prompt"
| summarize prompts = count(), avg_seconds = avg(duration) / 1000000000.0,
    by: { app.test.category, app.outcome, app.blocked_by }
| sort prompts desc
```

Prompts blocked by the Python checks (jailbreak, blocked terms, politics, length) have no guardrail child spans, because those checks run outside NeMo Guardrails, so `app.outcome` and `app.blocked_by` on the `prompt` span are how you see them. This needs image 0.7 or later. The LLM-based blocks also produce guardrail spans.

The process exits after `LOADGEN_MAX_ITERATIONS` (200) prompts and Kubernetes restarts it, so a slow memory leak can't build up.

## Troubleshooting

* **`ErrImagePull` or `ImagePullBackOff`:** the tag in `app.yaml` doesn't exist in the registry, or the repository is private and needs an `imagePullSecret` (step 3).
* **Model calls fail:** run `./test-nim-endpoints.sh`. Also check that the services resolve from the app pod:
  `kubectl -n nim exec deploy/nat-chat-app -- curl -s http://nemotron-70b-instruct.nim-service:8000/v1/models`
* **No traces in Dynatrace:** check `./deploy-otel-collector.sh status`, and check that `OTEL_OTLP_ENDPOINT` matches the collector Service.
* **`forbidden` errors in the collector logs:** the RBAC in `otel/otel-collector.yaml` didn't apply. Your kubectl user needs permission to create a Role in `gpu-operator`.

## Remove

```bash
kubectl delete -f app.yaml
./deploy-otel-collector.sh delete
```
