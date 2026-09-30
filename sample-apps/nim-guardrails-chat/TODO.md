# TODO

## Package Updates

| Package | Current pin | Latest | Recommendation | Reason |
|---|---|---|---|---|
| `opentelemetry-sdk` | `>=1.40.0,<1.41.0` | **1.45.0** | ⚠️ Update all three OTel together | 5 minor versions behind. Only update if you also move `traceloop-sdk` and `opentelemetry-instrumentation-langchain` simultaneously — they must stay in lockstep. |
| `traceloop-sdk` | `==0.51.1` | **0.62.4** | ⚠️ Update all three OTel together | Significant jump. Verify compatibility with the new OTel SDK version before updating. |
| `opentelemetry-instrumentation-langchain` | `==0.52.6` | **0.62.4** | ⚠️ Update all three OTel together | Version matches `traceloop-sdk` — that's not a coincidence, update as a set. |
| `streamlit` | `>=1.55.0,<1.56.0` | **1.64.0** | 🟡 Hold | 9 minor versions behind but no functional issue. Breaking UI changes are common between Streamlit minors — only update if you need a specific new feature. |
| `usearch` | `~=2.21.0` | **2.26.2** | 🟡 Hold | No issues reported, minor patch updates only. |

### Order of operations

1. OTel trio (`opentelemetry-sdk`, `traceloop-sdk`, `opentelemetry-instrumentation-langchain`) — all at once, then test tracing
