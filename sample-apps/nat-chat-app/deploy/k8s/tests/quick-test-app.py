"""Quick test of the deployed chat app: asks one question with and without NeMo Guardrails.

Run it inside the running app pod (see deploy/k8s/README.md, "Quick test"):

    kubectl -n nim exec -i deploy/nat-chat-app -c app -- python - < quick-test-app.py
    kubectl -n nim exec -i deploy/nat-chat-app -c app -- python - "Your question" --mode with < quick-test-app.py

It uses Streamlit's AppTest to load app.py from the pod's working directory (/app) and drive the
page headlessly: type the question, pick the guardrails option, click Submit. No browser or web
server is involved. The real app code runs, including the calls to the NIM services and the trace
export, in a new Python process inside the existing pod, so what it tests is the image the pod runs.

Every run gets a TEST_RUN_ID, stamped on the traces as app.test.run.id, so a run can be found in
Dynatrace without relying on timestamps. Exit code is 1 if any mode raised an exception, showed an
error, or produced no answer.
"""
import argparse
import logging
import os
import re
import sys
import time
import uuid

OPTIONS = {
    "with": "With NeMo Guardrails",
    "without": "Without NeMo Guardrails",
}

parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
parser.add_argument("prompt", nargs="?", default="What is Dynatrace?")
parser.add_argument("--mode", choices=["with", "without", "both"], default="both")
parser.add_argument("--app", default="app.py", help="path to the Streamlit script (default: app.py)")
args = parser.parse_args()

# Read by app.py at submit time and added to the traces. Must be set before the script runs.
run_id = os.environ.setdefault("TEST_RUN_ID", uuid.uuid4().hex[:12])

from streamlit.testing.v1 import AppTest  # noqa: E402

# Streamlit warns "missing ScriptRunContext" when a script runs outside `streamlit run`, which is
# expected here. A filter is used because Streamlit resets logger levels when the script starts.
logging.getLogger("streamlit.runtime.scriptrunner_utils.script_run_context").addFilter(
    lambda record: "missing ScriptRunContext" not in record.getMessage()
)

ANSWER_BOX = re.compile(r'<div class="status-box success-box">(.*)</div>', re.DOTALL)


def run_once(option):
    started = time.time()
    at = AppTest.from_file(args.app, default_timeout=300).run()
    at.text_area[0].set_value(args.prompt)
    at.selectbox[0].set_value(option)
    at.button[0].click().run()

    answers = [m.group(1) for m in (ANSWER_BOX.search(md.value) for md in at.markdown) if m]
    return {
        "option": option,
        "seconds": time.time() - started,
        "exceptions": [e.value for e in at.exception],
        "errors": [e.value for e in at.error],
        "status": [s.value for s in at.success if not s.value.startswith(("Dynatrace", "<"))],
        "warnings": [w.value for w in at.warning],
        "answer": answers[0] if answers else "",
    }


modes = ["without", "with"] if args.mode == "both" else [args.mode]
results = [run_once(OPTIONS[mode]) for mode in modes]

print(f"TEST_RUN_ID: {run_id}")
failed = False
for r in results:
    ok = bool(r["answer"]) and not r["exceptions"] and not r["errors"]
    failed = failed or not ok
    print(f"\n=== {r['option']}: {'PASS' if ok else 'FAIL'} ({r['seconds']:.1f}s) ===")
    print("EXCEPTIONS:", r["exceptions"])
    print("ERRORS    :", r["errors"])
    print("STATUS    :", r["status"])
    print("WARNINGS  :", r["warnings"])
    print("ANSWER    :", r["answer"])

# Spans are exported as they end (Traceloop runs with disable_batch=True), so there is nothing to wait
# for. This only forces out anything still queued before the process exits.
from opentelemetry import trace  # noqa: E402

provider = trace.get_tracer_provider()
if hasattr(provider, "force_flush"):
    provider.force_flush()

print(f"""
Find the traces in Dynatrace (one per mode, same app.test.run.id):

  fetch spans, from:now()-1h
  | filter app.test.run.id == "{run_id}"
  | fields trace.id, app.request.id, app.guardrails.enabled, start_time, duration
""")
sys.exit(1 if failed else 0)
