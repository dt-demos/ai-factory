"""Continuous prompt generator for the nat-chat-app: a weighted mix of questions that should be
answered and questions that the guardrails should block.

It runs inside a pod that uses the app image (see loadgen.yaml and loadgen.sh), loads app.py with
Streamlit's AppTest like quick-test-app.py does, and submits one prompt at a time. Each prompt carries an
expected outcome (prompts.yaml). The generator works out what the app actually did from the page
and logs a line per prompt, flagging any that differ from what was expected.

Traces: every prompt is stamped app.test.run.id = "loadgen:<category>" and app.test.category =
"<category>", so generated traffic can be told apart from real users, and sliced by category, in
Dynatrace. The app also records app.outcome and app.blocked_by on the prompt span:

    fetch spans | filter startsWith(app.test.run.id, "loadgen:") and span.name == "prompt"
    | summarize prompts = count(), by: { app.test.category, app.outcome, app.blocked_by }

Settings (flag or environment variable):
    --delay-min / LOADGEN_DELAY_MIN       seconds to wait between prompts, lower bound (60)
    --delay-max / LOADGEN_DELAY_MAX       upper bound (180)
    --iterations / LOADGEN_MAX_ITERATIONS prompts before exiting so the pod restarts clean (200, 0 = no limit)
    --weights / LOADGEN_WEIGHTS           override category weights, e.g. "blocked_terms=0,valid_with_guardrails=50"
    --prompts / LOADGEN_PROMPTS           prompt file (prompts.yaml next to this script)
    --app / LOADGEN_APP                   Streamlit script to drive (app.py)
    LOADGEN_VERBOSE=1                     also print the app's own log output for every prompt
    --list                                show the plan and estimated rate, then exit
    --once                                run one prompt now, no delay; exit 1 if it was unexpected
"""
import argparse
import contextlib
import io
import json
import logging
import os
import random
import re
import signal
import sys
import time
from pathlib import Path

import yaml

OPTIONS = {"with": "With NeMo Guardrails", "without": "Without NeMo Guardrails"}

# What the app prints and shows (app.py): the log line carrying the ids, the input-blocked label,
# and the answer box.
REQUEST_LINE = re.compile(r"Request (\w+) trace\.id=(\w+)")
BLOCKED_LABEL = re.compile(r"Input Blocked: \*\*(.+?)\*\*")
ANSWER_BOX = re.compile(r'<div class="status-box success-box">(.*)</div>', re.DOTALL)

# Rough average seconds a prompt occupies the app, used only for the --list rate estimate.
ESTIMATE_PASS_SECONDS = 18
ESTIMATE_BLOCKED_SECONDS = 1

stop_requested = False


def env_int(name, default):
    return int(os.environ.get(name, default))


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--prompts", default=os.environ.get("LOADGEN_PROMPTS") or str(Path(__file__).with_name("prompts.yaml")))
    p.add_argument("--app", default=os.environ.get("LOADGEN_APP", "app.py"))
    p.add_argument("--delay-min", type=float, default=env_int("LOADGEN_DELAY_MIN", 60))
    p.add_argument("--delay-max", type=float, default=env_int("LOADGEN_DELAY_MAX", 180))
    p.add_argument("--iterations", type=int, default=env_int("LOADGEN_MAX_ITERATIONS", 200))
    p.add_argument("--weights", default=os.environ.get("LOADGEN_WEIGHTS", ""))
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--once", action="store_true")
    p.add_argument("--list", action="store_true", dest="list_plan")
    return p.parse_args()


def load_categories(path, weights_override):
    """Return {name: {weight, mode, expect, prompts}} with generated prompts built and weights overridden."""
    raw = yaml.safe_load(Path(path).read_text())["categories"]
    overrides = {}
    for item in filter(None, (x.strip() for x in weights_override.split(","))):
        name, _, value = item.partition("=")
        if name not in raw:
            sys.exit(f"LOADGEN_WEIGHTS: unknown category {name!r}; known: {', '.join(raw)}")
        overrides[name] = float(value)

    categories = {}
    for name, c in raw.items():
        if "generate" in c:
            prompts = [c["generate"]["repeat"] * int(c["generate"]["times"])]
        else:
            prompts = list(c["prompts"])
        categories[name] = {
            "weight": overrides.get(name, float(c["weight"])),
            "mode": c["mode"],
            "expect": c["expect"],
            "prompts": prompts,
        }
    if not any(c["weight"] > 0 for c in categories.values()):
        sys.exit("all category weights are 0")
    return categories


def print_plan(categories, delay_min, delay_max):
    total = sum(c["weight"] for c in categories.values())
    pass_share = sum(c["weight"] for c in categories.values() if c["expect"] == "pass") / total
    avg_run = pass_share * ESTIMATE_PASS_SECONDS + (1 - pass_share) * ESTIMATE_BLOCKED_SECONDS
    cycle = (delay_min + delay_max) / 2 + avg_run
    per_hour = 3600 / cycle
    print(f"{'category':26} {'share':>6} {'mode':8} {'prompts':>7}  expected")
    for name, c in categories.items():
        print(f"{name:26} {100 * c['weight'] / total:5.1f}% {c['mode']:8} {len(c['prompts']):7}  {c['expect']}")
    print(f"\ndelay {delay_min:.0f}-{delay_max:.0f}s between prompts -> about {per_hour:.0f} prompts/hour, "
          f"{per_hour * 24:.0f}/day")
    print(f"prompts that should be answered: {100 * pass_share:.0f}% -> about {per_hour * pass_share:.0f} web searches/hour, "
          f"{per_hour * pass_share * 24:.0f}/day, {per_hour * pass_share * 24 * 30:.0f}/month (check your Tavily plan)")


def classify(at):
    """Work out what the app did from the Streamlit page. Returns (outcome, detail)."""
    if len(at.exception):
        return "exception", str(at.exception[0].value)[:200]
    errors = [e.value for e in at.error]
    for message in errors:
        match = BLOCKED_LABEL.search(message)
        if match:
            return f"blocked:{match.group(1)}", ""
    for message in errors:
        if "NAT Workflow Error" in message:
            return "workflow_error", message[:200]
    if any("Output Blocked" in w.value for w in at.warning):
        return "output_blocked", ""
    if any(ANSWER_BOX.search(md.value) for md in at.markdown):
        return "pass", ""
    return "no_answer", (errors[0] if errors else "")[:200]


def matches_expectation(expect, outcome):
    """expect is "any", "pass", "blocked:<label>", or "blocked:<label>|<label>" for several acceptable labels."""
    if expect == "any":
        return True
    if expect.startswith("blocked:"):
        return outcome in {f"blocked:{label}" for label in expect[len("blocked:"):].split("|")}
    return expect == outcome


def run_once(AppTest, app_path, prompt, mode, run_id, category):
    """Submit one prompt. Returns (outcome, detail, seconds, request_id, trace_id, app_output)."""
    # Read by app.py at submit time and added to the traces as app.test.run.id / app.test.category.
    os.environ["TEST_RUN_ID"] = run_id
    os.environ["TEST_CATEGORY"] = category
    buffer = io.StringIO()
    started = time.time()
    try:
        # The app prints its progress and the request/trace ids; capture them for the log line.
        with contextlib.redirect_stdout(buffer):
            at = AppTest.from_file(app_path, default_timeout=300).run()
            at.text_area[0].set_value(prompt)
            at.selectbox[0].set_value(OPTIONS[mode])
            at.button[0].click().run()
        outcome, detail = classify(at)
    except Exception as exc:  # AppTest raises on timeouts and script failures
        outcome, detail = "exception", f"{type(exc).__name__}: {exc}"[:200]
    app_output = buffer.getvalue()
    ids = REQUEST_LINE.search(app_output)
    return (outcome, detail, time.time() - started,
            ids.group(1) if ids else None, ids.group(2) if ids else None, app_output)


class Stats:
    def __init__(self):
        self.by_category = {}
        self.total = 0
        self.unexpected = 0

    def add(self, category, matched, seconds):
        c = self.by_category.setdefault(category, {"runs": 0, "unexpected": 0, "seconds": 0.0})
        c["runs"] += 1
        c["seconds"] += seconds
        c["unexpected"] += 0 if matched else 1
        self.total += 1
        self.unexpected += 0 if matched else 1

    def summary(self):
        lines = [f"--- summary: {self.total} prompts, {self.unexpected} unexpected ---"]
        for name, c in sorted(self.by_category.items()):
            lines.append(f"  {name:26} runs={c['runs']:4} unexpected={c['unexpected']:3} avg={c['seconds'] / c['runs']:5.1f}s")
        return "\n".join(lines)


def sleep_interruptibly(seconds):
    end = time.time() + seconds
    while not stop_requested and time.time() < end:
        time.sleep(min(1.0, end - time.time()))


def main():
    global stop_requested
    args = parse_args()
    # AppTest resolves a relative path against the file that calls it (this one, in /loadgen), not
    # the working directory, so pin it to the working directory (/app in the pod) here.
    args.app = str(Path(args.app).expanduser().resolve())
    categories = load_categories(args.prompts, args.weights)
    delay_min, delay_max = sorted((args.delay_min, args.delay_max))

    if args.list_plan:
        print_plan(categories, delay_min, delay_max)
        return 0

    if args.seed is not None:
        random.seed(args.seed)
    verbose = os.environ.get("LOADGEN_VERBOSE", "") not in ("", "0", "false")
    iterations = 1 if args.once else args.iterations

    def request_stop(signum, _frame):
        global stop_requested
        stop_requested = True
        print(f"signal {signum} received, stopping after the current prompt", flush=True)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    from streamlit.testing.v1 import AppTest

    # Streamlit warns "missing ScriptRunContext" outside `streamlit run`; expected here. A filter is
    # used because Streamlit resets logger levels when the script starts.
    logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").addFilter(
        lambda record: "missing ScriptRunContext" not in record.getMessage()
    )

    names = [n for n, c in categories.items() if c["weight"] > 0]
    weights = [categories[n]["weight"] for n in names]
    print_plan({n: categories[n] for n in names}, delay_min, delay_max)
    print(flush=True)

    stats = Stats()
    last_unexpected = False
    count = 0
    while not stop_requested and (iterations == 0 or count < iterations):
        count += 1
        category = random.choices(names, weights)[0]
        spec = categories[category]
        prompt = random.choice(spec["prompts"])
        run_id = f"loadgen:{category}"

        outcome, detail, seconds, request_id, trace_id, app_output = run_once(
            AppTest, args.app, prompt, spec["mode"], run_id, category)
        matched = matches_expectation(spec["expect"], outcome)
        last_unexpected = not matched
        stats.add(category, matched, seconds)

        print(json.dumps({
            "n": count, "category": category, "mode": spec["mode"], "expected": spec["expect"],
            "outcome": outcome, "match": matched, "seconds": round(seconds, 1),
            "request_id": request_id, "trace_id": trace_id, "run_id": run_id,
            "prompt": prompt if len(prompt) <= 80 else prompt[:77] + "...", "detail": detail or None,
        }), flush=True)
        if verbose or not matched or outcome in ("exception", "workflow_error", "no_answer"):
            for line in app_output.strip().splitlines()[-15:]:
                print(f"  | {line}", flush=True)
        if count % 10 == 0:
            print(stats.summary(), flush=True)

        if (iterations == 0 or count < iterations) and not stop_requested:
            sleep_interruptibly(random.uniform(delay_min, delay_max))

    if count % 10:  # not already printed inside the loop
        print(stats.summary(), flush=True)
    if not stop_requested and iterations and count >= iterations and not args.once:
        print(f"reached {iterations} prompts, exiting so the pod restarts with a clean process", flush=True)
    return 1 if (args.once and last_unexpected) else 0


if __name__ == "__main__":
    sys.exit(main())
