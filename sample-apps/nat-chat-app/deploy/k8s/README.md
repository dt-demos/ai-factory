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
