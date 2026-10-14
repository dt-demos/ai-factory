# Local OTel Collector

Runs the [Dynatrace OTel Collector](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector) as a Docker container on your machine, so the app can send traces, metrics and logs to Dynatrace when you [run it locally](../README.md). It listens on `localhost:4317` (gRPC) and `localhost:4318` (HTTP). For the in-cluster collector, see [deploy/k8s](../../k8s/README.md).

## Files

| File | Use |
|---|---|
| [`start-otel.sh`](start-otel.sh) | Reads `.env` and starts the container |
| [`stop-otel.sh`](stop-otel.sh) | Stops and removes the container |
| [`config.yaml`](config.yaml) | Default. OTLP in, Dynatrace out. This is all the cloud profile needs |
| [`config-nim.yaml`](config-nim.yaml) | Also scrapes `/v1/metrics` from NIM containers you run yourself on `NIM_HOST`, ports 8001-8003 |

## Prerequisites

* Docker
* A Dynatrace API token with the scopes `openTelemetryTrace.ingest`, `metrics.ingest` and `logs.ingest`

## Settings

The scripts read `sample-apps/nat-chat-app/.env`, the same file the app and the k8s scripts use. Values in the file override variables already set in your shell. If you haven't created it yet, run `cp .env.template .env` in the app root. Then uncomment and fill in the LOCAL section lines:

| Variable | Notes |
|---|---|
| `DT_BASE_URL` | `https://<tenant>.live.dynatrace.com` |
| `DT_API_TOKEN` | The token above |
| `NIM_HOST` | Only for `config-nim.yaml`: the IP of the host running your NIM containers |

Point the app at the collector with `OTEL_OTLP_ENDPOINT` in the same file:

| How you run the app | `OTEL_OTLP_ENDPOINT` |
|---|---|
| Docker | `http://host.docker.internal:4318` |
| Python | `http://localhost:4318` |

Or set `OTEL_SDK_DISABLED=true` to run without a collector.

## Run

From this folder:

```bash
./start-otel.sh                      # default config.yaml
./start-otel.sh config-nim.yaml      # also scrape your own NIM containers
./stop-otel.sh
```

`start-otel.sh` stops with an error if `DT_BASE_URL` or `DT_API_TOKEN` is missing. If a container named `otel-collector` already exists, it is started or left running as is, so run `./stop-otel.sh` first after you change the config or `.env`.

## Check it

```bash
docker logs otel-collector --tail 50
```

Look for export errors. A `401` or `403` from Dynatrace means the token is wrong or is missing a scope.
