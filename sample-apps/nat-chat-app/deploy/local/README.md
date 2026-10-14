# Run locally (cloud profile)

The app runs on your machine and calls the models on the NVIDIA cloud APIs (`CONFIG_TYPE=cloud`). You can run it with Python or with Docker.

## Prerequisites

* NVIDIA API key from [build.nvidia.com](https://build.nvidia.com)
* Tavily API key
* Python 3.11 or Docker
* Optional, for traces in Dynatrace: a Dynatrace environment and API token (see [OTel Collector](#optional-otel-collector))

Create your `.env` file in the app root, then uncomment the LOCAL section and fill in your keys:

```bash
cd sample-apps/nat-chat-app
cp .env.template .env
```

## Option A: Python

Run from the app root (`sample-apps/nat-chat-app`):

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

set -a; source .env; set +a                     # export the variables from .env
export OTEL_OTLP_ENDPOINT=http://localhost:4318  # the collector runs on localhost, not host.docker.internal

python update_config.py cloud                    # activates config.yml.cloud
streamlit run app.py
```

## Option B: Docker

Use the published image, or build your own (see [Building and pushing the image](../../README.md#building-and-pushing-the-image)). Run from the app root, where `.env` is:

```bash
export IMAGE=docker.io/dtdemos/nat-chat-app:0.7
```

Run detached:

```bash
docker run -d --name nat-chat-app -p 8501:8501 --env-file .env $IMAGE
```

Or run interactively, with logs in the terminal (useful for debugging):

```bash
docker run -it --rm --name nat-chat-app -p 8501:8501 --env-file .env $IMAGE
```

The container runs `update_config.py` with `CONFIG_TYPE` on startup, so no extra step is needed.

Open the app at `http://localhost:8501` with either option.

## Optional: OTel Collector

The [otel](otel/) folder runs the [Dynatrace OTel Collector](https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector) as a Docker container. It listens on `localhost:4317` (gRPC) and `localhost:4318` (HTTP).

| Config | Use |
|---|---|
| `config.yaml` (default) | OTLP in, Dynatrace out. This is all the cloud profile needs. |
| `config-nim.yaml` | Also scrapes `/v1/metrics` from NIM containers you run yourself on `NIM_HOST`, ports 8001–8003 |

Create an API token with the scopes `openTelemetryTrace.ingest`, `metrics.ingest` and `logs.ingest`, then:

```bash
cd deploy/local/otel
cp .env-otel-template .env      # set DT_BASE_URL and DT_API_TOKEN (and NIM_HOST for config-nim.yaml)
source .env

./start-otel.sh                 # or: ./start-otel.sh config-nim.yaml
./stop-otel.sh
```

If you don't run a collector, set `OTEL_SDK_DISABLED=true` in `.env`.
