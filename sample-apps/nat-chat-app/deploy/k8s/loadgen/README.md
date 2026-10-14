# Continuous load generator

Sends a steady, mixed stream of prompts to the nat-chat-app: questions that should be answered, and questions that the guardrails should block. Use it to keep Dynatrace dashboards populated and to check the guardrails over time.

It runs inside the cluster as its own pod, from the same image as the app, and drives the real `app.py` headlessly (the same way [`../quick-test-app.py`](../quick-test-app.py) does). Each prompt has an expected outcome, and the generator logs a line saying whether the app did what was expected.

| File | What it is |
|---|---|
| [`loadgen.sh`](loadgen.sh) | The command you run: start, stop, status, logs, delete |
| [`loadgen.py`](loadgen.py) | The generator. Runs in the pod |
| [`prompts.yaml`](prompts.yaml) | The prompts, their categories, weights and expected outcomes |
| [`loadgen.yaml`](loadgen.yaml) | The Kubernetes Deployment. `loadgen.sh` fills in the image and applies it |

## Quick start

Run from this folder on a machine with `kubectl` access to the cluster. The app must already be deployed (`../deploy-app.sh start`).

```bash
./loadgen.sh start      # begin sending prompts
./loadgen.sh status     # is it running, latest summary, unexpected results
./loadgen.sh logs       # follow the log
./loadgen.sh stop       # pause
```

## Commands

| Command | What it does |
|---|---|
| `./loadgen.sh start` | Creates or updates the `nat-chat-loadgen` ConfigMap (`loadgen.py` and `prompts.yaml`) and the Deployment, then restarts it and waits until it is ready. Also how you resume after `stop` and how you pick up an edited `prompts.yaml` |
| `./loadgen.sh stop` | Scales the Deployment to 0. Nothing is deleted |
| `./loadgen.sh status` | Shows the deployment and pod, the latest summary, the last 10 unexpected results and the last prompt |
| `./loadgen.sh logs` | Follows the log (the last 50 lines, then new ones) |
| `./loadgen.sh delete` | Removes the Deployment and the ConfigMap |

`start` uses the image the `nat-chat-app` deployment is running, so the generator always matches the app. To use a different image, set `IMAGE`:

```bash
IMAGE=docker.io/dtdemos/nat-chat-app:0.8 ./loadgen.sh start
```

## Settings

Settings are environment variables. The defaults are in `loadgen.yaml` (the pod) and as fallbacks in `loadgen.py`.

| Variable | Default | What it does |
|---|---|---|
| `LOADGEN_DELAY_MIN` | `60` | Shortest wait between prompts, in seconds |
| `LOADGEN_DELAY_MAX` | `180` | Longest wait. The wait is random between min and max, and starts after the previous prompt finishes |
| `LOADGEN_MAX_ITERATIONS` | `200` | Prompts to send before exiting, so Kubernetes restarts the process clean. `0` means never exit |
| `LOADGEN_WEIGHTS` | empty | Overrides category weights, for example `blocked_terms=0,valid_with_guardrails=50`. Unlisted categories keep their `prompts.yaml` weight. A weight of 0 turns a category off |
| `LOADGEN_VERBOSE` | off | Set to `1` to print the app's own log output for every prompt, not only for unexpected ones |
| `LOADGEN_PROMPTS` | `prompts.yaml` next to the script | Path to a different prompt file |
| `LOADGEN_APP` | `app.py` | The Streamlit script to drive. Only relevant outside the pod |

### Changing settings

Put the variables in front of `./loadgen.sh start`:

```bash
LOADGEN_DELAY_MIN=30 LOADGEN_DELAY_MAX=90 ./loadgen.sh start        # faster, about 45 prompts an hour
LOADGEN_DELAY_MIN=120 LOADGEN_DELAY_MAX=300 ./loadgen.sh start      # slower, about 14 an hour
LOADGEN_WEIGHTS="valid_with_guardrails=0,valid_without_guardrails=0" ./loadgen.sh start   # blocked prompts only, no web searches
```

**Settings last only until the next `start`.** Running `./loadgen.sh start` again resets everything to the defaults in `loadgen.yaml`, so repeat the variables each time, or edit the values in `loadgen.yaml`.

### Rate and cost

At the defaults the generator sends about **27 prompts an hour** (measured; about 650 a day) and restarts after 200 prompts, roughly every 7½ hours.

- About 55% of prompts are ones that should be answered. Each one makes **one web search (Tavily)** and usually **two 70B model calls**, taking 15–25 s. That is about 15 searches an hour. Check your Tavily plan before leaving it running.
- The 70B model is shared with real users, so continuous load slows them down.
- Blocked prompts are cheap: a prompt stopped by a Python rule takes well under a second and uses no model, and one stopped by an LLM check takes a fraction of a second.

## The prompt mix

Categories in [`prompts.yaml`](prompts.yaml), taken from [`../../../PROMPTS.md`](../../../PROMPTS.md). The share is each weight divided by the total (100).

| Category | Share | Guardrails | Prompts | Expected outcome |
|---|---|---|---|---|
| `valid_with_guardrails` | 35% | with | 8 | answered |
| `valid_without_guardrails` | 20% | without | 7 | answered |
| `blocked_content_safety` | 8% | with | 5 | blocked by `Content Safety` or `Topic Control` |
| `blocked_topic_control` | 8% | with | 5 | blocked by `Topic Control` |
| `blocked_jailbreak` | 8% | with | 7 | blocked by `Jailbreak Detection` |
| `blocked_terms` | 8% | with | 8 | blocked by `Blocked Terms` |
| `blocked_politics` | 8% | with | 7 | blocked by `Politics Filter` |
| `blocked_length` | 5% | with | 1, generated (about 2,600 characters) | blocked by `Input Length Limit` |

Content safety and topic control run side by side, so either can stop a harmful prompt. That is why `blocked_content_safety` accepts both labels.

### Editing prompts

`prompts.yaml` has, for each category: `weight`, `mode` (`with` or `without` guardrails), `expect`, and `prompts` (a list) or `generate` (a text repeated N times). `expect` is one of:

- `pass`: the question should be answered.
- `blocked:<label>`: the input should be blocked, where `<label>` is what the app shows: `Jailbreak Detection`, `Blocked Terms`, `Input Length Limit`, `Politics Filter`, `Content Safety` or `Topic Control`.
- `blocked:<a>|<b>`: blocked by either label.
- `any`: no check.

After editing, run `./loadgen.sh start` to load the new file.

## Reading the results

### The log

Each prompt produces one JSON line:

```json
{"n": 3, "category": "valid_with_guardrails", "mode": "with", "expected": "pass", "outcome": "pass", "match": true, "seconds": 20.8, "request_id": "…", "trace_id": "…", "run_id": "loadgen:valid_with_guardrails", "prompt": "How does Kubernetes auto-scaling work?", "detail": null}
```

`outcome` is what the app did, worked out from the page: `pass`, `blocked:<label>`, `output_blocked`, `workflow_error`, `exception` or `no_answer`. `match` is `false` when it differs from `expected`. `request_id` and `trace_id` link the line to the trace in Dynatrace. For a `match: false` result, an unexpected answer, an error or an exception, the lines that follow (marked `  |`) show the app's own output for that prompt.

A summary prints after every 10 prompts and when it exits. `./loadgen.sh status` shows the latest one and any unexpected results.

An unexpected result is not always a bug. The LLM guardrails are models, so an unexpected block or pass can be a real finding about the guardrails, and a mismatch on one of the LLM-based categories is the most likely to be this.

### In Dynatrace

Every generated prompt has `app.test.run.id = "loadgen:<category>"` and `app.test.category = "<category>"` on its trace, and the app records `app.outcome` and `app.blocked_by`. The pod name (`nat-chat-loadgen-…`) also separates it from real users.

```
fetch spans, from:now()-1h
| filter startsWith(app.test.run.id, "loadgen:") and span.name == "prompt"
| summarize prompts = count(), avg_seconds = avg(duration) / 1000000000.0,
    by: { app.test.category, app.outcome, app.blocked_by }
| sort prompts desc
```

`app.outcome` is `passed`, `blocked_input`, `blocked_output`, `workflow_error` or `guardrail_error`. Prompts stopped by the Python rules (jailbreak, blocked terms, length, politics) have no guardrail child spans, so these attributes are how you see them. They need image 0.7 or later.

## Running the script yourself

`loadgen.py` can be run directly for a quick look at the plan. `--list` needs only PyYAML:

```bash
python3 loadgen.py --list     # categories, shares, and the estimated rate and web searches
```

Other flags, for use inside a pod that has the app (for example with `kubectl exec`): `--once` sends a single prompt now with no delay and exits 1 if the outcome was unexpected, `--iterations N`, `--delay-min`, `--delay-max`, `--weights`, `--prompts`, `--app` and `--seed` (for a repeatable choice of prompts). Each flag has the matching environment variable above.

## Troubleshooting

- **The pod doesn't start:** check `kubectl -n nim describe pod -l app=nat-chat-loadgen`. An image pull error usually means the `dockerhub` pull secret is missing from the `nim` namespace.
- **`start` fails to read the image:** the `nat-chat-app` deployment doesn't exist yet. Deploy the app first, or set `IMAGE`.
- **Many `workflow_error`, `exception` or `no_answer` lines:** read the `  |` lines under them. Common causes are a NIM or Tavily being unreachable or rate-limited.
- **`Retrying after stale event loop binding: Event loop is closed` in the log:** a harmless warning from NeMo Guardrails' HTTP client, which retries after the app moves to a new event loop for each prompt. It adds at most half a second.
- **Settings went back to the defaults:** `./loadgen.sh start` resets them. Pass the variables again.
