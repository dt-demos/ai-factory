# environment variables used:
# - OTEL_OTLP_ENDPOINT: OpenTelemetry OTLP exporter endpoint (default: http://localhost:4317)

import asyncio
import contextlib
import io
import logging
import warnings
import os
import re
import sys
import time
import uuid
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ------------------------------------------------------------------------------
# Logging – output to both console and app.log
# ------------------------------------------------------------------------------
_LOG_FILE = Path(__file__).parent / "app.log"

# Truncate log file once at process start (not on Streamlit reruns)
if "APP_LOG_INITIALIZED" not in os.environ:
    _LOG_FILE.write_text("", encoding="utf-8")
    os.environ["APP_LOG_INITIALIZED"] = "1"


def log(msg: str = ""):
    """Log a message to both console and app.log."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}"
    print(line, flush=True)
    with open(_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# Suppress Pydantic warnings BEFORE importing libraries that use Pydantic
warnings.filterwarnings("ignore", message=".*validate_default.*", module="pydantic")

# for streamlit app
import streamlit as st

# authlib (pulled in by NAT) adds its own "always" filter for its deprecation warning when imported,
# which overrides the blanket "ignore" set in configure_logging(). Import it first, then silence it.
try:
    from authlib.deprecate import AuthlibDeprecationWarning
    warnings.filterwarnings("ignore", category=AuthlibDeprecationWarning)
except ImportError:
    pass

# for NVIDIA Guardrails
from nemoguardrails import RailsConfig, LLMRails
from nemoguardrails.tracing.adapters.opentelemetry import OpenTelemetryAdapter
from nemoguardrails.tracing.adapters.registry import register_log_adapter
from nemoguardrails.tracing.spans import SpanKind as GuardrailsSpanKind

# for OpenTelemetry and Dynatrace Traceloop
from opentelemetry import context as otel_context
from opentelemetry import trace
from traceloop.sdk import Traceloop
from traceloop.sdk.instruments import Instruments

# ------------------------------------------------------------------------------
# constants
# ------------------------------------------------------------------------------
# name used in tracing
SERVICE_NAME = "nat-chat-app" + ("-" + os.getenv("SERVICE_NAME_SUFFIX", "") if os.getenv("SERVICE_NAME_SUFFIX", "") else "")
# used in UI selectbox
OPTION_WITH_GUARDRAILS = "With NeMo Guardrails"
OPTION_WITHOUT_GUARDRAILS = "Without NeMo Guardrails"

# ------------------------------------------------------------------------------
# traceloop-sdk setup for intrumentation and Dynatrace OTLP ingestion
# ------------------------------------------------------------------------------

# disable SDK collects anonymous telemetry data
os.environ['TRACELOOP_TELEMETRY'] = "false"
# set metrics preference to delta since this is what Dynatrace API expects
os.environ['OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE'] = "delta"

@st.cache_resource(show_spinner="Initializing Traceloop SDK...")
def initialize_traceloop():
    """Initialize Traceloop once (cached to prevent re-initialization on every Streamlit rerun)."""

    if os.getenv('OTEL_SDK_DISABLED', 'false').lower() == 'true':
        log("⚠️  OTEL_SDK_DISABLED=true — skipping Traceloop initialization")
        return

    log("✓ Initializing Traceloop SDK...")
    # Pod identity (downward API env vars in deploy/k8s/app.yaml) so traces from concurrent
    # processes in the same pod, or different pods, can be told apart.
    pod_name = os.getenv("POD_NAME")
    resource_attributes = {
        key: value for key, value in {
            "k8s.pod.name": pod_name,
            "service.instance.id": pod_name,
            "k8s.namespace.name": os.getenv("POD_NAMESPACE"),
        }.items() if value
    }
    Traceloop.init(
        app_name=SERVICE_NAME,
        api_endpoint=os.environ.get('OTEL_OTLP_ENDPOINT', 'http://localhost:4318'),
        disable_batch=True,
        should_enrich_metrics=True,
        resource_attributes=resource_attributes,
        # Traceloop's SQLAlchemy instrumentor is gated by Instruments.PYMYSQL. This app makes no
        # SQL calls and the installed sqlalchemy is newer than the instrumentor supports, which
        # logs "DependencyConflict" at startup.
        block_instruments={Instruments.PYMYSQL},
    )
    log("✓ Traceloop SDK initialized with: "+ os.environ.get('OTEL_OTLP_ENDPOINT', 'http://localhost:4318'))
    
# ------------------------------------------------------------------------------
# Suppress verbose NAT agent logging and warnings
# ------------------------------------------------------------------------------
@st.cache_resource(show_spinner="Configuring logging...")
def configure_logging():
    """Configure logging levels once (cached to prevent re-execution on every Streamlit rerun)."""
    log("✓ Configuring logging levels...")
    logging.getLogger("nat.agent").setLevel(logging.CRITICAL)
    logging.getLogger("nat").setLevel(logging.CRITICAL)
    logging.getLogger("langchain_community").setLevel(logging.ERROR)
    logging.getLogger("nemoguardrails.actions.action_dispatcher").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")
    return True

# ------------------------------------------------------------------------------
# Config file validation
# ------------------------------------------------------------------------------
@st.cache_resource(show_spinner="Config file validation...")
def ensure_config_files_exist():
    """Verify required config files exist before app starts."""
    log("✓ Ensuring required config files exist...")

    # Construct config file paths
    guardrails_config_path = Path("guardrails_config/config.yml")
    nat_config_path = Path("src/nat_simple_web_query/configs/config.yml")
    
    # Ensure both config files exist
    missing_files = []
    for config_path in [guardrails_config_path, nat_config_path]:
        if not config_path.exists():
            missing_files.append(str(config_path))
    
    if missing_files:
        log(f"❌ Error: The following config files were not found:")
        for file in missing_files:
            log(f"   - {file}")
        log(f"   Please run 'python update_config.py <config_type>' to generate config files.")
        os._exit(1)  # Force immediate exit without cleanup
    
    log("✓ All required config files found")

# ------------------------------------------------------------------------------
# guardrail functions
# ------------------------------------------------------------------------------
class InternalKindOpenTelemetryAdapter(OpenTelemetryAdapter):
    """NeMo Guardrails OpenTelemetry adapter that emits the top-level guardrails.request span as INTERNAL.

    NeMo marks that span SERVER, so Dynatrace treats every guardrails call as an extra incoming
    request next to the app's own "prompt" span. INTERNAL keeps "prompt" as the only request root.
    """

    def _create_span(self, span_data, *args, **kwargs):
        if span_data.span_kind == GuardrailsSpanKind.SERVER:
            span_data.span_kind = GuardrailsSpanKind.INTERNAL
        return super()._create_span(span_data, *args, **kwargs)

# adapter name referenced by tracing.adapters in guardrails_config/config.yml.*
GUARDRAILS_OTEL_ADAPTER = "OpenTelemetryInternal"

@st.cache_resource(show_spinner="Configuring guardrails...")
def initialize_guardrails():
    """Initialize guardrails configuration once (cached to prevent re-initialization on every Streamlit rerun)."""
    log("✓ Initializing guardrails configuration...")
    try:
        try:
            register_log_adapter(InternalKindOpenTelemetryAdapter, GUARDRAILS_OTEL_ADAPTER)
        except ValueError:
            pass  # already registered (Streamlit re-runs this script inside the same process)

        # Load guardrails configuration
        guardrails_path = Path(__file__).parent / "guardrails_config"
        
        if not guardrails_path.exists():
            log(f"⚠️ Guardrails config not found at: {guardrails_path}")
            return None
        
        rails_config = RailsConfig.from_path(str(guardrails_path))
        rails = LLMRails(rails_config)
        
        # Register custom guardrails actions
        import sys
        sys.path.insert(0, str(guardrails_path))
        
        try:
            from actions import (
                check_jailbreak, 
                check_blocked_terms, 
                check_input_topic, 
                check_input_length,
                check_output_relevance,
                check_politics
            )
            
            rails.register_action(check_jailbreak, "check_jailbreak")
            rails.register_action(check_input_topic, "check_input_topic")
            rails.register_action(check_output_relevance, "check_output_relevance")
            rails.register_action(check_blocked_terms, "check_blocked_terms")
            rails.register_action(check_input_length, "check_input_length")
            rails.register_action(check_politics, "check_politics")
            
            log("✓ Guardrails initialized successfully")
            return rails
        except ImportError as e:
            log(f"⚠️ Could not load custom actions: {e}")
            return rails
            
    except Exception as e:
        log(f"❌ Error initializing guardrails: {e}")
        return None

@st.cache_resource
def get_event_loop():
    """One event loop for the life of the process.

    The cached guardrails client and NAT workflow hold async HTTP connections bound to the
    loop they were created on. A fresh loop per query (then closed) leaves them dead, which
    surfaces as "the handler is closed" on the second and later queries.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    return loop


def _rails_only_content(result) -> str:
    """Return the message text from a rails-only generate_async() result.

    With options={"rails": [...]} NeMo Guardrails does not call the main LLM. It echoes
    the checked message when every rail passes, or returns the refusal text when a rail blocks.
    """
    response = getattr(result, "response", result)
    if isinstance(response, list):
        response = response[-1] if response else {}
    if isinstance(response, dict):
        return str(response.get("content", ""))
    return str(response)

def _blocking_rail_label(result) -> str:
    """Name the LLM guardrail that stopped a rails-only input check, from the generation log.

    Content safety and topic control run side by side, so either (or both) can block; the first
    rail in the log that stopped is reported. Falls back to a generic label if the log is missing.
    """
    for rail in getattr(getattr(result, "log", None), "activated_rails", None) or []:
        if not rail.stop:
            continue
        name = (rail.name or "").lower()
        if name.startswith("content safety"):
            return "Content Safety"
        if name.startswith("topic safety"):
            return "Topic Control"
    return "LLM Guardrails"

async def check_input_guardrails(rails, user_input):
    """Apply input guardrails and return (is_safe, message).

    Python checks run first (fast, no LLM call). If all pass, the LLM-based
    content safety and topic control checks run via rails.generate_async(). This lets us
    always report which specific guardrail blocked the input.
    """
    from actions import check_jailbreak, check_blocked_terms, check_input_length, check_politics

    context = {"user_message": user_input, "last_user_message": user_input}
    refusal = "I'm sorry, I can't respond to that request. Please rephrase your question."

    python_checks = [
        ("Jailbreak Detection",  check_jailbreak),
        ("Blocked Terms",        check_blocked_terms),
        ("Input Length Limit",   check_input_length),
        ("Politics Filter",      check_politics),
    ]
    for check_name, check_fn in python_checks:
        if check_fn(context=context):
            log(f"🚫 Input blocked by: {check_name}")
            return False, f"**{check_name}** — {refusal}"

    # All Python checks passed — run the LLM-based checks (content safety, topic control)
    guardrail_timeout = int(os.getenv("GUARDRAIL_TIMEOUT_SECONDS", "30"))
    try:
        start_time = time.time()
        # Input rails only: runs content safety / topic control (small NIMs) and skips the
        # main-LLM reply generation and output rails that a default call would also run.
        # The activated-rails log says which rail stopped the prompt.
        input_result = await asyncio.wait_for(
            rails.generate_async(
                messages=[{"role": "user", "content": user_input}],
                options={"rails": ["input"], "log": {"activated_rails": True}},
            ),
            timeout=guardrail_timeout,
        )
        duration = time.time() - start_time
        log(f"⏱️  Input guardrail (LLM checks) execution time: {duration:.2f} seconds")

        # Rails-only echoes the input when it passes; anything else is a rail's refusal.
        if _rails_only_content(input_result).strip() != user_input.strip():
            blocked_by = _blocking_rail_label(input_result)
            log(f"🚫 Input blocked by: {blocked_by}")
            return False, f"**{blocked_by}** — {refusal}"

        return True, "Input passed guardrails"
    except asyncio.TimeoutError:
        log(f"⚠️  Input guardrail timed out after {guardrail_timeout}s — passing input through")
        return True, "Input passed guardrails (timeout)"
    except Exception as e:
        if "TCPTransport closed" in str(e) or "handler is closed" in str(e):
            log("⚠️  Input guardrail hit a stale connection — passing input through")
            return True, "Input passed guardrails (stale connection)"
        return False, f"Error checking input: {str(e)}"

async def check_output_guardrails(rails, user_input, workflow_result):
    """Apply output guardrails and return (is_safe, message)."""
    guardrail_timeout = int(os.getenv("GUARDRAIL_TIMEOUT_SECONDS", "30"))
    try:
        start_time = time.time()
        # Output rails only, applied to the real workflow answer. A default call would
        # regenerate a reply with the main LLM and check that instead of the answer.
        output_result = await asyncio.wait_for(
            rails.generate_async(
                messages=[
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": workflow_result}
                ],
                options={"rails": ["output"]},
            ),
            timeout=guardrail_timeout,
        )
        end_time = time.time()
        duration = end_time - start_time
        log(f"⏱️  Output guardrail execution time: {duration:.2f} seconds")

        # Rails-only echoes the answer when it passes; anything else is a rail's refusal.
        content = _rails_only_content(output_result)
        if content.strip() != str(workflow_result).strip():
            return False, content

        return True, "Output passed guardrails"
    except asyncio.TimeoutError:
        log(f"⚠️  Output guardrail timed out after {guardrail_timeout}s — passing output through")
        return True, "Output passed guardrails (timeout)"
    except Exception as e:
        return False, f"Error checking output: {str(e)}"
    
# ------------------------------------------------------------------------------
# NAT functions
# ------------------------------------------------------------------------------

# Module-level cache: keeps the workflow context alive across Streamlit reruns
# so load_workflow() only runs once per process instead of on every query.
_nat_workflow_cache: dict = {}

async def _get_nat_workflow(nat_config_path: str):
    """Return a cached workflow instance, initializing it on first call."""
    if "workflow" not in _nat_workflow_cache:
        from nat.runtime.loader import load_workflow
        log("⏳ Initializing NAT workflow (first query only)...")
        start = time.time()
        loader = load_workflow(nat_config_path)
        _nat_workflow_cache["workflow"] = await loader.__aenter__()
        _nat_workflow_cache["loader"] = loader
        log(f"⏱️  NAT workflow initialized in {time.time() - start:.2f}s")
    return _nat_workflow_cache["workflow"]

async def run_nat_workflow(user_input, nat_config_path):
    """Execute the NAT workflow."""
    stderr_capture = io.StringIO()

    try:
        workflow = await _get_nat_workflow(nat_config_path)
        with contextlib.redirect_stderr(stderr_capture):
            start_time = time.time()
            async with workflow.run(user_input) as runner:
                workflow_result = await runner.result(to_type=str)
            end_time = time.time()
            duration = end_time - start_time
            log(f"⏱️  NAT workflow execution time: {duration:.2f} seconds")
        result_str = str(workflow_result)
        # Detect raw tool-call tokens leaked from the model's intermediate reasoning
        if "<|tool_call>" in result_str or result_str.strip().startswith("call:"):
            log(f"⚠️  NAT workflow returned raw tool-call fragment — treating as incomplete response")
            return False, "The AI model returned an incomplete response. Please try again."
        return True, result_str
    except Exception as e:
        error_msg = str(e)
        if "Failed to parse agent output" in error_msg:
            # The ReAct agent got a reply it could not parse (for example "Thought:" with no Action),
            # which is not a safety refusal.
            log(f"⚠️  NAT agent output could not be parsed: {error_msg[:300]}")
            return False, "The AI agent returned a response it could not parse. Please try again."
        if "list index out of range" in error_msg or "Failed to parse" in error_msg:
            return False, "Request was refused by the AI model for safety reasons"
        return False, f"Error: {error_msg}"

# ------------------------------------------------------------------------------
# Main functions
# ------------------------------------------------------------------------------
def describe_outcome(results):
    """Summarize a process_query() result as (app.outcome, app.blocked_by) for the prompt span.

    The Python guardrail checks run outside NeMo Guardrails and leave no spans of their own, so
    without this a prompt they block looks like any other request that did no work.
    """
    if not results["input_safe"]:
        message = results["input_message"]
        if message.startswith("Error checking input"):
            return "guardrail_error", None
        match = re.search(r"\*\*(.+?)\*\*", message)  # "**Politics Filter** — I'm sorry..."
        return "blocked_input", match.group(1) if match else "unknown"
    if not results["workflow_success"]:
        return "workflow_error", None
    if not results["output_safe"]:
        if results["output_message"].startswith("Error checking output"):
            return "guardrail_error", None
        return "blocked_output", "Output Guardrails"
    return "passed", None

async def process_query(user_input, user_option_guardrail, rails, nat_config_path, status_text=None):
    """Main processing function that coordinates all steps."""
    log(f"Processing {user_option_guardrail} query: {user_input}")
    # The "prompt" span (and its request id properties) is current here. Remember it: the LangChain
    # instrumentation can leave a finished task span attached after the workflow returns, which
    # would otherwise become the parent of the output guardrails spans.
    prompt_context = otel_context.get_current()
    results = {
        "input_safe": False,
        "input_message": "",
        "workflow_success": False,
        "workflow_result": "",
        "output_safe": False,
        "output_message": "",
        "final_result": ""
    }
    
    if user_option_guardrail == OPTION_WITH_GUARDRAILS:
        # Step 1: Input guardrails
        status_text.text("⚡ Running input guardrails...")
        results["input_safe"], results["input_message"] = await check_input_guardrails(rails, user_input)
        if not results["input_safe"]:
            return results
    else:
        results["input_safe"] = True
    
    # Step 2: Run NAT workflow
    status_text.text("⚡ Running NAT workflow...")
    results["workflow_success"], results["workflow_result"] = await run_nat_workflow(user_input, nat_config_path)
    if not results["workflow_success"]:
        return results
    
    # Check for safety refusal in NAT workflow result
    if "Invalid Format" in results["workflow_result"] and (
        "can't" in results["workflow_result"].lower() or 
        "illegal" in results["workflow_result"].lower()
    ):
        results["workflow_success"] = False
        results["workflow_result"] = "(NAT workflow) Request was refused by the AI model for safety reasons. Details: " + results["workflow_result"]
        return results
    
    # Step 3: Output guardrails
    if user_option_guardrail == OPTION_WITH_GUARDRAILS:
        status_text.text("⚡ Running Output guardrails...")
        context_token = otel_context.attach(prompt_context)
        try:
            results["output_safe"], results["output_message"] = await check_output_guardrails(
                rails, user_input, results["workflow_result"]
            )
        finally:
            otel_context.detach(context_token)
        if results["output_safe"]:
            results["final_result"] = results["workflow_result"]
    else:
        # Output guardrails disabled - set as safe and use workflow result
        results["output_safe"] = True
        results["final_result"] = results["workflow_result"]
    
    return results

# ------------------------------------------------------------------------------
# streamlit Setup
# ------------------------------------------------------------------------------

def main():
    """Main Streamlit application."""
    # Verify config files exist first (will exit if missing)
    ensure_config_files_exist()
    
    # Initialize components will caching to prevent re-initialization on every rerun
    initialize_traceloop()
    configure_logging()
    rails = initialize_guardrails() 

    # Page configuration (must be first Streamlit command)
    st.set_page_config(
        page_title="NVIDIA Agent Toolkit with Guardrails with Dynatrace Observability",
        page_icon="🤖",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Custom CSS for better styling
    st.markdown("""
        <style>
        .main-header {
            font-size: 2.5rem;
            font-weight: bold;
            color: #76B900;
            text-align: center;
            margin-bottom: 1rem;
        }
        .sub-header {
            font-size: 1.4rem;
            color: #666;
            text-align: center;
            margin-bottom: 2rem;
        }
        .status-box {
            padding: 1rem;
            border-radius: 0.5rem;
            margin: 1rem 0;
            color: #212529;
        }
        .success-box {
            background-color: #d4edda;
            border-left: 4px solid #28a745;
        }
        .warning-box {
            background-color: #fff3cd;
            border-left: 4px solid #ffc107;
        }
        .error-box {
            background-color: #f8d7da;
            border-left: 4px solid #dc3545;
        }
        .info-box {
            background-color: #d1ecf1;
            border-left: 4px solid #17a2b8;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # Header
    # Alt tagline - NVIDIA NeMo Agent Toolkit, NeMo Guardrails and Dynatrace Insights"
    st.markdown('<div class="main-header">Operationalizing AI at Scale</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-header">Built on Dell. Powered by NVIDIA. Observed by Dynatrace.</div>', unsafe_allow_html=True)
    st.markdown('<div style="text-align: center;"><img src="app/static/dell-dynatrace.png" alt="Dynatrace and NVIDIA" width="300" style="margin: auto; display: block;"></div>', unsafe_allow_html=True)

    # Main content area
    col1, col2, col3 = st.columns([1, 3, 1])
    with col2:
        # Initialize session state
        if 'history' not in st.session_state:
            st.session_state.history = []

        # Show Service name
        st.markdown(f'<hr><p><div style="text-align: left; font-style: italic;">Service Name: {SERVICE_NAME}</div></p>', unsafe_allow_html=True)

        # Query input
        user_input = st.text_area(
            "💬 Ask your question",
            placeholder="Enter your question here...",
            height=100,
            help="Type your question and press Submit"
        )
            
        user_option_guardrail = st.selectbox(
            label="Select an option",
            options=[OPTION_WITHOUT_GUARDRAILS, OPTION_WITH_GUARDRAILS],
            index=0,
            help="Choose an option to run with or without NeMo guardrails")
                
        col_submit, col_clear = st.columns([1, 1])
        
        with col_submit:
            submit_button = st.button("🚀 Submit Query", type="primary", use_container_width=True)
        
        with col_clear:
            clear_button = st.button("🗑️ Clear History", use_container_width=True)
        
        if clear_button:
            st.session_state.history = []
            st.rerun()
        
        # Process query
        nat_config_path = str(Path(__file__).parent / "src" / "nat_simple_web_query" / "configs" / "config.yml")
        if submit_button and user_input.strip():
            if not rails:
                st.error("❌ Cannot process query: Guardrails not initialized")
            elif not Path(nat_config_path).exists():
                st.error(f"❌ Config file not found: {nat_config_path}")
            else:
                with st.spinner("🔄 Processing your query..."):
                    with trace.get_tracer(SERVICE_NAME).start_as_current_span(name="prompt", kind=trace.SpanKind.SERVER) as span:
                        # Unique ids to find this request's trace by more than a timestamp.
                        # app.test.run.id (TEST_RUN_ID) groups the runs of smoke.py and loadgen, and
                        # app.test.category (TEST_CATEGORY) says which kind of prompt loadgen sent.
                        request_attributes = {
                            "app.request.id": uuid.uuid4().hex,
                            "app.guardrails.enabled": user_option_guardrail == OPTION_WITH_GUARDRAILS,
                        }
                        if os.getenv("TEST_RUN_ID"):
                            request_attributes["app.test.run.id"] = os.environ["TEST_RUN_ID"]
                        if os.getenv("TEST_CATEGORY"):
                            request_attributes["app.test.category"] = os.environ["TEST_CATEGORY"]
                        for key, value in request_attributes.items():
                            span.set_attribute(key, value)
                        # Also stamps the same ids on the child spans (traceloop.association.properties.*)
                        Traceloop.set_association_properties(request_attributes)
                        log(f"Request {request_attributes['app.request.id']} trace.id={span.get_span_context().trace_id:032x}")

                        # Create progress indicators
                        progress_bar = st.progress(50)
                        status_text = st.empty()
                        
                        # Step 1: Input guardrails
                        if user_option_guardrail == OPTION_WITH_GUARDRAILS:
                            status_text.text("⚡ Checking Input guardrails...")
                        else:
                            status_text.text("⚡ Skipping guardrails...")
                        
                        # Run async processing
                        loop = get_event_loop()
                        asyncio.set_event_loop(loop)  # Streamlit may run each rerun on a different thread
                        results = loop.run_until_complete(process_query(user_input, user_option_guardrail, rails, Path(nat_config_path), status_text=status_text))

                        # What happened to the prompt, and which guardrail stopped it if one did.
                        outcome, blocked_by = describe_outcome(results)
                        span.set_attribute("app.outcome", outcome)
                        if blocked_by:
                            span.set_attribute("app.blocked_by", blocked_by)

                        # Update progress
                        progress_bar.progress(100)
                        status_text.empty()
                        progress_bar.empty()
                        
                        # Display results
                        st.divider()
                        
                        # Input check result
                        if not results["input_safe"]:
                            st.error(f"🚫 Input Blocked: {results['input_message']}")
                        else:
                            if user_option_guardrail == OPTION_WITH_GUARDRAILS:
                                st.success("✅ Input passed safety checks")
                            
                            # Workflow result
                            if not results["workflow_success"]:
                                st.error(f"⚠️ NAT Workflow Error: {results['workflow_result']}")
                            else:
                                st.success("✅ NAT Workflow completed successfully")
                                
                                if user_option_guardrail == OPTION_WITH_GUARDRAILS:
                                    # Output check result
                                    if not results["output_safe"]:
                                        st.warning(f"⚠️ Output Blocked: {results['output_message']}")
                                    else:
                                        st.success("✅ Output passed safety checks")
                                
                                # Only display and store final result if output guardrails passed or are disabled
                                if results["final_result"]:  # final_result is only set if guardrails passed or are disabled
                                    # Final result
                                    st.markdown("### 📝 Response:")
                                    st.markdown(f'<div class="status-box success-box">{results["final_result"]}</div>', 
                                            unsafe_allow_html=True)
                                    
                                    # Add to history
                                    st.session_state.history.append({
                                        "question": user_input,
                                        "answer": results["final_result"]
                                    })
        
        # Display conversation history
        if st.session_state.history:
            st.divider()
            st.header("📜 Conversation History")
            
            for idx, item in enumerate(reversed(st.session_state.history)):
                with st.expander(f"Q{len(st.session_state.history) - idx}: {item['question'][:80]}...", expanded=(idx == 0)):
                    st.markdown("**Question:**")
                    st.info(item['question'])
                    st.markdown("**Answer:**")
                    st.success(item['answer'])

# ------------------------------------------------------------------------------
# App entry point
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    main()