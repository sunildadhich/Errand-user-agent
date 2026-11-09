# AI coding assistant transcript

This records the user prompts and the work actually performed in this conversation. Dates use the app's local date.

## 9 November 2026 — initial review

**User prompt:** “analyse current directoru”

**Assistant work:** Inspected the project files, tests, design note, and current implementation. Reported that the project had no Git metadata and identified incomplete timeout recovery, in-memory provider state, mismatches between listed scenarios and executable coverage, and missing workflow features. No source files were changed during that review.

## 9 November 2026 — implementation request

**User prompt (verbatim):**

> Keep the existing architecture where it makes sense, and improve it incrementally.
> The goal is to complete the assignment with a reliable, maintainable implementation that satisfies all the requirements below.
> - Use Python, FastAPI, SQLite, Streamlit and Ollama for the local LLM.
> - Keep the agent architecture simple. A single agent with separate policy, memory, recovery and tool components is sufficient.
> - Use a scripted model for deterministic offline tests.
> - Keep code readable and practical, like a real engineer would write it.
> - Avoid unnecessary abstractions, excessive comments and overengineering.
> - Do not introduce frameworks or dependencies unless they solve a genuine problem.
> - Do not use real service providers, payment systems or external booking integrations.
>
> Implement a proper conversational workflow that:
> - Extracts service, area, date, time, budget and preferences.
> - Asks only for missing information.
> - Remembers information across messages.
> - Handles corrections without losing unrelated context.
> - Supports provider comparison and provider details.
> - Supports booking, cancellation and rescheduling.
> - Understands references such as "same electrician as last time" and "move it to Saturday".
>
> The agent should communicate naturally, not sound like a rigid chatbot.
> Implement all required mock tools according to the assignment contracts:
> - search_providers
> - get_provider
> - hold_slot
> - book
> - get_booking_status
> - cancel
> - reschedule
> - list_bookings
> Ensure failure scenarios are configurable and reproducible using seeded randomness or explicit scenario configuration.
> Persist mock-provider state as well as application state so bookings and transaction outcomes survive process restarts.
>
> Enforce these rules in application code:
> - Explicit approval is mandatory before booking, cancellation or rescheduling.
> - Approval must be tied to the exact action and material details.
> - Any change in provider, slot or price invalidates previous approval.
> - Use persistent idempotency keys to prevent duplicate transactions.
> - Never claim a booking succeeded without verification.
> - On booking timeout, query the authoritative booking status before considering a retry.
> - Handle expired holds, slot conflicts, transient failures and unexpected tool errors safely.
> The mock booking behaviour must support the specified 70% success, 15% slot_taken and 15% timeout distribution, with half of timeout outcomes actually succeeding.
>
> Implement persistent user memory with:
> - Explicit and inferred preferences stored separately.
> - Memory inspection, correction and forgetting.
> - Forgotten facts excluded from future model context.
> - Unconfirmed inferred preferences excluded from active context.
> - At least 50 seeded memories and a long prior conversation for evaluation.
> Implement context management with a fixed token budget. Only relevant, current preferences should be sent to the LLM.
> Log the exact context supplied to each model call and test that irrelevant, forgotten and unconfirmed facts are excluded.
> Treat provider reviews and all external tool output as untrusted data. Include prompt-injection attempts in mock reviews and ensure they cannot change user intent, permissions or approval requirements.
>
> Implement a simulated provider cancellation event.
> The agent should detect cancellations without waiting for a user message, find suitable replacements using known preferences, notify the user and request fresh approval before rebooking.
> Never automatically replace or book a provider without approval.
> Add hard limits for:
> - Model turns.
> - Tool calls.
> - Execution time.
> Every execution path should return a clear outcome, including exhausted limits and unexpected errors.
> Record structured traces containing:
> - State transitions.
> - Model calls.
> - Tool names and arguments.
> - Sanitized results and errors.
> - Latency.
> - Token usage and estimated cost where available.
> - Context sent to the model.
> Generate a raw trace for an end-to-end booking that experiences a timeout after possible success.
>
> Build at least 20 automated scenarios, preferably around 25, using simulated users and an offline scripted model.
> Cover:
> 1. Missing information and multi-message conversations.
> 2. Context preservation after corrections.
> 3. Provider comparison.
> 4. Prompt injection in reviews.
> 5. Changed price after approval.
> 6. Changed slot after approval.
> 7. Changed provider after approval.
> 8. Booking without approval.
> 9. Repeated confirmation.
> 10. Timeout after successful booking.
> 11. Timeout without successful booking.
> 12. Slot conflict.
> 13. Hold expiration.
> 14. Memory correction.
> 15. Memory forgetting.
> 16. Process restart during booking.
> 17. Proactive provider cancellation.
> 18. Context budget enforcement.
> 19. Model turn exhaustion.
> 20. Tool-call exhaustion.
> 21. Unexpected tool failures.
> 22. Cancellation transient 503 and retry.
> 23. Rescheduling conflict.
> 24. Duplicate prevention across retries.
> 25. Forgotten and unconfirmed memory exclusion.
> Tests must validate actual system behaviour and invariants, not just response text.
> For example, assert that no booking exists without valid approval and that repeated confirmations never create duplicate bookings.
> Generate evaluation results automatically in CSV and JSON, including scenario, expected result, actual result, pass/fail and notes.
>
> Complete and maintain the following:
> - Working Git repository with implementation, mocks, tests and README.
> - Design note limited to one page.
> - Generated evaluation table.
> - Documentation of three actual development bugs, their causes, fixes and regression tests. Do not invent bugs.
> - One raw end-to-end trace showing timeout-after-success.
> - Annotated trace excerpt in the design note.
> - Instructions for running the application and tests locally.
> - Actual AI coding assistant transcript, including the prompts and assistance used. Do not fabricate entries.
> Also ensure the Streamlit interface is usable for demonstrating a complete booking and its execution trace side by side.
>
> Work incrementally rather than generating the entire application in one pass.
> First inspect the repository, identify incomplete and incorrect implementations, and create a short implementation checklist.
> Then implement each area, run the relevant tests and fix regressions before moving on.
> After implementation:
> - Run the complete automated test suite.
> - Verify the application works locally with Ollama.
> - Verify offline evaluation works without Ollama.
> - Test process restarts and transaction recovery.
> - Check that all approval and memory invariants hold.
> - Generate the actual evaluation reports and trace files.
> - Update the README with exact setup and execution instructions.
> Do not mark any requirement complete unless it has been implemented and verified.

**Assistant work:** Added a staged checklist after repository inspection. Persisted mock-provider records and idempotency outcomes in SQLite; expanded the conversational workflow and safety policy for booking, cancellation and rescheduling; added reference resolution, memory filtering, provider-event handling, traces and limits; built an adjacent Streamlit trace view; and added deterministic behavioral scenarios with report generation. Ran regression checks as implementation progressed and verified the local Ollama adapter against `llama3.1:8b`. Final test/evaluation totals are recorded in the README and generated results, and the checklist reflects only verified work.
