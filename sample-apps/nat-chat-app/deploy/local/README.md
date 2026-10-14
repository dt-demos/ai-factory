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
cd sample-apps/nat-chat-app/
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

set -a; source .env; set +a                     # export the variables from .env

python update_config.py cloud                    # activates config.yml.cloud
streamlit run app.py
```

## Option B: Docker

Use the published image, or build your own (see [Building and pushing the image](../../README.md#building-and-pushing-the-image)). Run from the app root, where `.env` is:

```bash
export IMAGE=$(grep -o 'docker.io/dtdemos/nat-chat-app:[0-9.]*' deploy/k8s/app.yaml)   # the tag Kubernetes uses
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

The [otel](otel/README.md) folder runs the Dynatrace OTel Collector as a Docker container on `localhost:4317` (gRPC) and `localhost:4318` (HTTP). It reads `DT_BASE_URL` and `DT_API_TOKEN` from the same `.env` as the app (uncomment them in the LOCAL section):

```bash
cd deploy/local/otel
./start-otel.sh                 # or: ./start-otel.sh config-nim.yaml
./stop-otel.sh
```

See the [otel README](otel/README.md) for the token scopes, `config-nim.yaml` and troubleshooting. If you don't run a collector, set `OTEL_SDK_DISABLED=true` in `.env`.
