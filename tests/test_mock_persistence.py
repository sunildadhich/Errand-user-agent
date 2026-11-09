from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.tools.mock_services import MockErrandTools, Scenario, ToolError


def test_provider_state_survives_new_tool_instance(db):
    # Use the same durable database through distinct sessions, as after an app restart.
    engine = db.get_bind()
    Session = sessionmaker(bind=engine)
    first = MockErrandTools(Scenario(booking_mode="timeout_booked"), db=db)
    hold = first.hold_slot("p101", "10:30")
    try:
        first.book(hold["hold_id"], "approval-1")
    except ToolError as exc:
        assert exc.code == "timeout_after_success"
    second_db = Session()
    try:
        second = MockErrandTools(Scenario(), db=second_db)
        status = second.get_booking_status(hold["hold_id"], "approval-1")
        assert status["status"] == "confirmed"
        assert second.book(hold["hold_id"], "approval-1")["booking_id"] == status["booking_id"]
    finally:
        second_db.close()


def test_search_does_not_offer_unrelated_service(db):
    tools = MockErrandTools(db=db)
    assert tools.search_providers("window cleaning", "Central", "tomorrow") == []


def test_default_outcome_distribution_is_reproducible():
    def sample(seed):
        tools = MockErrandTools(Scenario(seed=seed))
        counts = {"success": 0, "slot_taken": 0, "timeout_booked": 0, "timeout_not_booked": 0}
        for _ in range(10000):
            counts[tools._booking_mode()] += 1
        return counts
    first, second = sample(123), sample(123)
    assert first == second
    assert abs(first["success"] / 10000 - .70) < .02
    assert abs(first["slot_taken"] / 10000 - .15) < .02
    assert abs((first["timeout_booked"] + first["timeout_not_booked"]) / 10000 - .15) < .02
    assert abs(first["timeout_booked"] - first["timeout_not_booked"]) < 200


def test_seeded_outcome_sequence_continues_across_tool_instances(db):
    outcomes = [MockErrandTools(Scenario(seed=7), db=db)._booking_mode() for _ in range(1000)]
    counts = {mode: outcomes.count(mode) for mode in ("success", "slot_taken", "timeout_booked", "timeout_not_booked")}
    assert all(counts.values())
    assert abs(counts["success"] / 1000 - .70) < .04
    assert abs(counts["slot_taken"] / 1000 - .15) < .04
    assert abs((counts["timeout_booked"] + counts["timeout_not_booked"]) / 1000 - .15) < .04
    assert abs(counts["timeout_booked"] - counts["timeout_not_booked"]) < 40
