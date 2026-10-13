# Kubernetes deployment

The NIM models run in the lab's `nim-service` namespace. This folder deploys the OTel Collector and the chat app into the `nim` namespace.

| File | Purpose |
|---|---|
| `otel-collector.yaml` | Dynatrace OTel Collector Deployment and Service ([raw manifest approach](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector/deployment#kubernetes--raw-manifest)) |
| `deploy-otel-collector.sh` | Creates the Dynatrace secret and the collector ConfigMap (from `../otel/config-dcgm-nim.yaml`), then applies `otel-collector.yaml` |
| `app-local.yaml` | Chat app (`CONFIG_TYPE=local`): Secret, ConfigMap, Deployment and LoadBalancer Service |

## OTel Collector

The collector receives OTLP from the app on `dynatrace-otel-collector.nim:4317/4318`. It also scrapes these Prometheus endpoints:

| Job | Target | Path |
|---|---|---|
| dcgm | every `nvidia-dcgm-exporter` pod in `gpu-operator`, found via EndpointSlice discovery (one pod per GPU node) | `/metrics` |
| llama-3.1-nemotron-70b-instruct | `nemotron-70b-instruct.nim-service:8000` | `/metrics` |
| llama-3.1-nemoguard-8b-content-safety | `content-safety.nim-service:8000` | `/v1/metrics` |
| llama-3.1-nemoguard-8b-topic-control | `topic-control.nim-service:8000` | `/v1/metrics` |
| nv-embedqa-e5-v5 | `embedqa-e5-v5.nim-service:8000` | `/v1/metrics` |

Deploy:

```bash
export DT_BASE_URL=https://<tenant>.live.dynatrace.com
export DT_API_TOKEN=<token>   # scopes: openTelemetryTrace.ingest, metrics.ingest, logs.ingest

./deploy-otel-collector.sh apply
./deploy-otel-collector.sh status
```

After you edit `../otel/config-dcgm-nim.yaml`, run `apply` again. It updates the ConfigMap and restarts the collector.

Check that metrics reach Dynatrace with this DQL query:

```
timeseries avg(num_requests_running), by: { model_name }
```

Remove:

```bash
./deploy-otel-collector.sh delete
```

## Chat app (`app-local.yaml`)

The app runs in the `nim` namespace and calls the lab models directly through the endpoints in `../guardrails_config/config.yml.local` and `../src/nat_simple_web_query/configs/config.yml.local`.

### Prerequisites

1. **Lab models are up.** Check them with `../../../scripts/test-nim-endpoints.sh`.
2. **The `nim` namespace exists.** `./deploy-otel-collector.sh apply` creates it. You can also run `kubectl create namespace nim`.
3. **The OTel Collector is deployed** (see above). It isn't required for the app to run, but traces sent to `OTEL_OTLP_ENDPOINT` are dropped without it.
4. **You have a Tavily API key** for web search. In local mode, the app doesn't use `NVIDIA_API_KEY`, so any value works.
5. **The cluster can pull the app image.** The manifest uses `nvidia-workshop-app:latest` with `imagePullPolicy: IfNotPresent` and no registry, so the image must already be on the GPU worker nodes or come from a registry the cluster can pull from. See step 1 below.

### 1. Build the image

`CONFIG_TYPE=local` copies `config.yml.local` into place when the container starts. Those files are copied into the image at build time, so rebuild the image after you edit either `config.yml.local`.

From `sample-apps/nim-guardrails-chat`:

```bash
docker build -t nvidia-workshop-app:latest .
```

Then do one of the following.

* **Push to a registry** (recommended). Tag and push the image, then change `image:` in `app-local.yaml`:
  ```bash
  docker tag nvidia-workshop-app:latest <registry>/nvidia-workshop-app:latest
  docker push <registry>/nvidia-workshop-app:latest
  ```
* **Import onto each node** (no registry, containerd runtime):
  ```bash
  docker save nvidia-workshop-app:latest -o app.tar
  for n in london-worker1 london-worker2; do
    scp app.tar $n:/tmp/ && ssh $n 'sudo ctr -n k8s.io images import /tmp/app.tar'
  done
  ```

### 2. Set the configuration

Edit `app-local.yaml` before you apply it, and don't commit real keys:

| Setting | Where | Value |
|---|---|---|
| `TAVILY_API_KEY` | Secret `app-secrets` | Your Tavily key |
| `NVIDIA_API_KEY` | Secret `app-secrets` | Not used in local mode. Leave the placeholder. |
| `CONFIG_TYPE` | ConfigMap `app-config` | `local` |
| `OTEL_OTLP_ENDPOINT` | ConfigMap `app-config` | `http://dynatrace-otel-collector.nim.svc.cluster.local:4318` |
| `GITHUB_USER` | ConfigMap `app-config` | Optional. Used to scope the service name per participant. |

The app doesn't read the `NVIDIA_MODEL_ENDPOINT_*` values in local mode.

### 3. Deploy

```bash
kubectl apply -f app-local.yaml
kubectl -n nim rollout status deployment nvidia-workshop-app
kubectl -n nim logs deploy/nvidia-workshop-app | head -20   # look for "Generating config for type: local"
```

After you change the ConfigMap or Secret, restart the app with `kubectl -n nim rollout restart deployment nvidia-workshop-app`.

### 4. Open the app

```bash
kubectl -n nim get svc nvidia-workshop-app
```

If `EXTERNAL-IP` stays `<pending>` (no load balancer in the lab), port-forward instead and open `http://localhost:8501`:

```bash
kubectl -n nim port-forward svc/nvidia-workshop-app 8501:80
```

### Troubleshooting

* **`ErrImagePull` or `ImagePullBackOff`:** the image isn't on the node or in a registry the cluster can reach (step 1).
* **Model calls fail:** from the app pod, check that the lab services resolve:
  `kubectl -n nim exec deploy/nvidia-workshop-app -- curl -s http://nemotron-70b-instruct.nim-service:8000/v1/models`
* **No traces in Dynatrace:** check `./deploy-otel-collector.sh status`, and check that `OTEL_OTLP_ENDPOINT` matches the collector Service.

Remove the app:

```bash
kubectl delete -f app-local.yaml
```
