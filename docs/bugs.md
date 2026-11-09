# Development bugs

These were observed while implementing and testing the assignment; each has a regression check.

1. **Provider state vanished when the process restarted.** Booking holds and idempotency results lived in Python dictionaries, so a new process could not reconcile a timed-out booking. State now lives in SQLite `mock_records`. Regression: `test_provider_state_survives_new_tool_instance` and scenario S16/S26.

2. **A normal sentence could trigger approval.** The initial confirmation check searched for approval words as substrings, so text such as “yesterday would have been better” contained `yes` and could approve a pending booking. Confirmation now accepts only exact affirmative replies. Regression: scenario S30, `test_30_non_confirmation_word_cannot_approve`.

3. **An unresolved timeout left no safe retry path.** The first workflow consumed its approval before the provider call; after a timeout with no booking, a later confirmation could not proceed because that approval was already consumed. The agent now checks authoritative status first, then creates a fresh hold and approval before any retry. Regression: scenario S26, `test_26_timeout_not_found_requires_fresh_approval_then_can_retry`.
