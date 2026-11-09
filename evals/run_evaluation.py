"""Run the deterministic acceptance suite and write machine-readable results."""
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent.model import ScriptedModel
from app.agent.orchestrator import ErrandAgent
from app.core.memory import MemoryManager
from app.database import Base
from app.models import Conversation, Trace
from app.tools.mock_services import MockErrandTools, Scenario
from tests.test_assignment_scenarios import SCENARIOS


def write_reports():
    out = ROOT / "evals" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (ROOT / "evals" / "scenarios.json").write_text(json.dumps(
        [{"id": scenario_id, "name": name, "expected": name} for scenario_id, name, _ in SCENARIOS],
        indent=2, ensure_ascii=False) + "\n")
    results = []
    for scenario_id, scenario, check in SCENARIOS:
        try:
            check()
            results.append({"scenario": scenario_id, "expected_result": scenario,
                            "actual_result": "behavioral assertions passed", "pass_fail": "PASS",
                            "notes": "Behavioral and invariant checks passed."})
        except Exception as exc:
            results.append({"scenario": scenario_id, "expected_result": scenario,
                            "actual_result": "behavioral assertions failed", "pass_fail": "FAIL",
                            "notes": f"{type(exc).__name__}: {exc}"})

    (out / "evaluation.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    with (out / "evaluation.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=results[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    memory = MemoryManager()
    memory.seed_evaluation_memories(db)
    db.add(Conversation(id="prior_eval_conversation", user_id="eval-user", state="collecting",
                        facts={"history": [f"Prior conversation turn {i}: discussed household service preferences." for i in range(100)]}))
    db.commit()

    tools = MockErrandTools(Scenario(booking_mode="timeout_booked"), db=db)
    agent = ErrandAgent(db, tools=tools, model=ScriptedModel())
    cid, _, _, _ = agent.handle("trace-user", "Find a plumber in Bellandur this week mornings under ₹1500")
    agent.handle("trace-user", "yes", cid)
    trace_rows = db.query(Trace).order_by(Trace.id).all()
    trace = [{"run_id": row.run_id, "event": row.event, "payload": row.payload,
              "created_at": row.created_at.isoformat()} for row in trace_rows]
    trace_path = ROOT / "traces" / "timeout_after_success.json"
    trace_path.parent.mkdir(exist_ok=True)
    trace_path.write_text(json.dumps({"conversation_id": cid, "scenario": "timeout_booked", "events": trace},
                                     indent=2, ensure_ascii=False) + "\n")
    db.close()
    return results, trace_path


if __name__ == "__main__":
    rows, trace_path = write_reports()
    passed = sum(row["pass_fail"] == "PASS" for row in rows)
    print(f"{passed}/{len(rows)} scenarios passed")
    print(f"Reports: {ROOT / 'evals' / 'results'}")
    print(f"Raw trace: {trace_path}")
    if passed != len(rows):
        raise SystemExit(1)
