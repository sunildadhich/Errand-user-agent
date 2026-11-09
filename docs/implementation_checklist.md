# Implementation checklist

Status is updated only after implementation and verification.

## Stages

- [x] **1. Workflow and conversation state:** extract and merge corrections; collect missing facts; compare providers and show details; support booking, cancellation, rescheduling and references to existing bookings.
- [x] **2. Durable mock tools and recovery:** persist provider holds and transactions in SQLite; configure seeded failure scenarios; implement authoritative status checks and safe retries for all required tools.
- [x] **3. Policy, memory and proactive events:** exact-action approvals for every consequential action; explicit/inferred memories with inspect, correct and forget; seeded evaluation memory; provider cancellation notification and fresh replacement approval.
- [x] **4. Limits, context and traces:** relevant context under a fixed token budget; log exact model context; enforce model/tool/time limits; structured sanitized traces with latency and token/cost estimates where available.
- [x] **5. Evaluation and regression tests:** 33 deterministic behavioral scenarios; assert safety and persistence invariants; generate CSV and JSON reports and a raw timeout-after-success trace.
- [x] **6. Demo and project deliverables:** usable side-by-side Streamlit chat/trace view; concise design note with annotated trace; three verified bug records; truthful assistant transcript; complete README and initialized Git repository.
- [x] **7. Final verification:** full test suite, offline evaluation, Ollama integration, process-restart recovery, approval/memory invariants, and local run instructions verified.

## Initial findings

- Existing FastAPI, Streamlit, SQLAlchemy/SQLite, Ollama adapter, scripted model, and basic booking workflow are a useful foundation.
- The state machine currently covers only booking; mock tool state is in memory; several evaluation scenarios are only names, without executable checks.
- An unknown booking timeout consumes the approval, preventing a later status recovery; a failed booking also consumes approval before the tool result is known.
- Provider search can return unrelated providers when there is no service match.
- Memory, cancellation, rescheduling, execution limits, context budgeting, and detailed model traces are not integrated into the current workflow.
- This directory has no Git metadata. Establishing the requested repository is included in stage 6.
