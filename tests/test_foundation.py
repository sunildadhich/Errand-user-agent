from app.agent.orchestrator import ErrandAgent
from app.agent.model import ScriptedModel
from app.tools.mock_services import MockErrandTools, Scenario
from app.models import BookingAttempt


def test_missing_details_are_asked_one_at_a_time(db):
    agent = ErrandAgent(db, model=ScriptedModel())
    cid, reply, state, _ = agent.handle("u1", "I need a plumber")
    assert state == "collecting"
    assert "area" in reply.lower()


def test_agent_finds_option_and_requests_approval(db):
    agent = ErrandAgent(db, model=ScriptedModel())
    cid, reply, state, _ = agent.handle("u1", "Find a plumber in Bellandur this week, mornings only under ₹1500")
    assert state == "awaiting_approval"
    assert "book it" in reply.lower()


def test_timeout_after_success_is_verified(db):
    scenario = Scenario(booking_mode="timeout_booked")
    tools = MockErrandTools(scenario)
    agent = ErrandAgent(db, tools=tools, model=ScriptedModel())
    cid, _, state, _ = agent.handle("u1", "Find a plumber in Bellandur this week, mornings only under ₹1500")
    cid, reply, state, data = agent.handle("u1", "yes, approve", cid)
    assert state == "booked"
    assert "booked" in reply.lower()
    assert data["booking_id"]


def test_no_booking_without_approval(db):
    agent = ErrandAgent(db, model=ScriptedModel())
    cid, _, state, _ = agent.handle("u1", "Find a plumber in Bellandur this week, mornings only under ₹1500")
    assert state == "awaiting_approval"
    assert len(agent.tools.bookings) == 0


def test_slot_taken_does_not_claim_success(db):
    tools = MockErrandTools(Scenario(booking_mode="slot_taken"))
    agent = ErrandAgent(db, tools=tools, model=ScriptedModel())
    cid, _, _, _ = agent.handle("u1", "Find a plumber in Bellandur this week, mornings only under ₹1500")
    cid, reply, state, _ = agent.handle("u1", "yes", cid)
    assert state == "error"
    assert "couldn't complete" in reply.lower()
    assert len(tools.bookings) == 0
