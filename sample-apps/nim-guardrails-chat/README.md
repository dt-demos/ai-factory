# NIM Guardrails Chat

![Architecture](static/architecture.png)

A Streamlit chat app that demonstrates NVIDIA NeMo Guardrails with NIM model endpoints. The app supports input safety rails (jailbreak detection, blocked terms, input length, politics filtering) and exports traces via OpenTelemetry.

## Prerequisites

- Docker installed and running
- NVIDIA API key from [build.nvidia.com](https://build.nvidia.com)
- Tavily API key (used for web search)
- Dynatrace OTel ingest endpoint and API token (for tracing)

## Models Used

* `google/gemma-4-31b-it` — The main conversational LLM, used for the NAT tool-calling agent workflow and the `check_politics` guardrail. Called via the NVIDIA cloud API (`integrate.api.nvidia.com`).

* `nvidia/nemotron-3.5-content-safety` — The safety classifier used for both content safety guardrails (detecting harmful or unsafe input/output) and topic control guardrails (blocking political content). Runs on both input and output rails.

* `nvidia/nv-embedqa-e5-v5` — The embedding model used by the NAT workflow for semantic search and retrieval.

## Docker Build

Run from inside this directory:

```bash
docker build -t nim-guardrails-chat .
```

## Running the App

Create a `.env` file:

```
CONFIG_TYPE=build
NVIDIA_API_KEY=nvapi-<your-key>
TAVILY_API_KEY=tvly-<your-key>
SERVICE_NAME=nim-guardrails-chat
# Enable tracing (optional):
#OTEL_OTLP_ENDPOINT=http://host.docker.internal:4318
# Or disable OTel entirely:
OTEL_SDK_DISABLED=true
```

Detached (background):
```bash
docker run -d \
  --name nim-guardrails-chat \
  -p 8501:8501 \
  --env-file .env \
  nim-guardrails-chat
```

Interactive (logs printed to terminal, useful for debugging):
```bash
docker run -it --rm \
  --name nim-guardrails-chat \
  -p 8501:8501 \
  --env-file .env \
  nim-guardrails-chat
```

The app will be available at `http://localhost:8501`.

## Useful Commands

```bash
# View logs
docker logs -f nim-guardrails-chat

# Stop and remove
docker stop nim-guardrails-chat && docker rm nim-guardrails-chat
```
