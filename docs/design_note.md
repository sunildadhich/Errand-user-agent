# Errand Agent design

The app uses one conversational agent with separate intent/model, policy, memory, trace, and mock-tool components. The model extracts explicit details from the latest message; application code owns facts, state transitions, approvals, and all provider side effects. Corrections merge into stored conversation facts, so unrelated details remain intact. Missing fields are requested one at a time. Existing bookings support comparison, details, cancellation, rescheduling, and references to a prior provider.

SQLite stores conversations, explicit and inferred memories, exact-action approvals, booking attempts, trace events, simulated provider state, and provider events. The mock provider has no external connections. Its seeded booking outcomes follow the configured 70/15/15 distribution. Persistent idempotency records prevent duplicate actions; on booking timeout, the app queries mock-provider status before reporting success or requesting a fresh approval for a retry.

Approval fingerprints bind the action to the material details. Booking, cancellation, and rescheduling require an explicit affirmative reply. A provider cancellation creates a replacement proposal and fresh approval, delivered through the Streamlit periodic notification check; no replacement is booked automatically. Provider reviews are untrusted display data and are excluded from model context. Only relevant active memories fit the configured context budget. Forgotten and unconfirmed inferred memories are filtered before model calls.

Each request writes structured state, model, and tool events. Model traces include the exact context and estimated token use; tool traces include arguments, sanitized results/errors, and elapsed time. The Streamlit view places the trace beside the conversation. The timeout-after-success trace is in `traces/timeout_after_success.json`.

## Annotated timeout-after-success trace excerpt

Excerpt from the generated raw trace (unrelated fields omitted; booking identifier shortened):

```json
{"event":"tool_call","payload":{"tool":"book","error":"timeout_after_success"}}
{"event":"tool_call","payload":{"tool":"get_booking_status","result":{"status":"confirmed","booking_id":"b_…"}}}
```

The booking call did not return a result, so the agent queried the authoritative status using the same persistent idempotency key. It reported the booking only after that query returned `confirmed`. The complete trace also records the prior approval details, provider hold, model context, arguments, and latency.

**Trade-offs.** SQLite keeps local setup simple and durable. The deterministic workflow is easier to test than an agent that chooses tools freely. The local model handles language variation, while the scripted model makes offline evaluation repeatable. Mock records are stored in the same SQLite database so process restarts exercise realistic recovery behavior.
