# Errand Agent

A local-first assistant for arranging home services. It uses a single deterministic workflow, a local Ollama model for language extraction, SQLite for durable application and mock-provider state, and a Streamlit chat/trace view. No real providers, payments, or external booking services are called.

## Requirements

- Python 3.11 or newer
- Ollama installed locally, with `llama3.1:8b` available (or set another local model)

## Install and run

From this directory:

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
ollama pull llama3.1:8b
ollama serve                     # keep this terminal running
```

In a second terminal, start the API:

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

In a third terminal, start the interface:

```bash
source .venv/bin/activate
streamlit run frontend/streamlit_app.py
```

Open the Streamlit URL printed by the command. The interface shows the conversation and its execution trace side by side. It checks provider cancellation events every five seconds. The simulated provider-cancellation endpoint is `POST /mock/provider-cancel/{booking_id}`; the UI will show a replacement proposal and wait for new approval.

Change `.env` to use a different local model, database, or the offline model:

```dotenv
MODEL_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
DATABASE_URL=sqlite:///./errand_agent.db
MAX_MODEL_TURNS=6
MAX_TOOL_CALLS=12
MAX_RUN_SECONDS=30
CONTEXT_TOKEN_BUDGET=1800
```

The application runs a small SQLite schema migration at startup to add the confirmed-memory field to an existing database. Mock holds, idempotency records, bookings, cancellations, and reschedules are persisted in the same database.

## Offline verification and reports

No Ollama service is needed for the scripted suite or reports:

```bash
source .venv/bin/activate
MODEL_PROVIDER=scripted pytest -q
MODEL_PROVIDER=scripted python evals/run_evaluation.py
```

The suite covers 35 acceptance scenarios and 79 individual test cases. The evaluation command runs those same behavioral checks and writes:

- `evals/results/evaluation.csv`
- `evals/results/evaluation.json`
- `traces/timeout_after_success.json` — raw end-to-end trace for a booking that succeeds at the provider but times out at the call boundary

The trace records the exact model context, state transitions, tool arguments, sanitized outputs/errors, latency, and estimated token usage. Ollama token counts/cost are recorded only when the local model provides them; no external pricing is assumed.

## Safety and workflow

- The model extracts details and helps interpret messages. Application code owns state transitions and provider actions.
- Booking, cancellation, and rescheduling require an explicit approval for the exact action and material details. A changed provider, slot, or quote invalidates the approval.
- Booking attempts use persistent idempotency keys. After a timeout, the agent checks authoritative mock-provider status before asking for fresh approval to retry.
- Provider reviews are treated as untrusted display content and are never included in the model context.
- Explicit memories are active. Inferred memories remain inactive until confirmed; forgotten memories are excluded from context.
- The mock booking distribution is 70% success, 15% slot-taken, and 15% timeout, with timeout split evenly between success-after-timeout and no booking. A seeded scenario makes runs reproducible; explicit modes support targeted evaluation.

## Project notes

- [Design note](docs/design_note.md)
- [Implementation checklist](docs/implementation_checklist.md)
- [Development bugs and regression tests](docs/bugs.md)
- [Actual assistant transcript](docs/coding_assistant_transcript.md)
- [Evaluation scenarios](evals/scenarios.json)
