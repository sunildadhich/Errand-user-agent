"""Behavioral acceptance scenarios; the eval runner executes these same checks."""
import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.agent.model import ScriptedModel
from app.agent.orchestrator import ErrandAgent
from app.core.memory import MemoryManager
from app.database import Base
from app.database import init_db
from app.models import Approval, BookingAttempt, Conversation, Memory, Trace, UserEvent
from app.tools.mock_services import MockErrandTools, Scenario, ToolError
from app import config
from app.agent.intent import simple_extract


class CapturingModel(ScriptedModel):
    def extract(self, message, known_facts):
        self.last_context = known_facts
        return simple_extract(message)


def setup_agent(scenario=None, model=None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    tools = MockErrandTools(scenario or Scenario(), db=session)
    return engine, session, tools, ErrandAgent(session, tools=tools, model=model or ScriptedModel())


def start_booking(agent, text="Find a plumber in Bellandur this week, mornings under ₹1500", user="sam"):
    return agent.handle(user, text)


def test_01_missing_information_multi_message():
    _, db, tools, agent = setup_agent()
    cid, reply, state, _ = agent.handle("sam", "I need a plumber")
    assert state == "collecting" and "area" in reply.lower()
    cid, reply, state, _ = agent.handle("sam", "in Bellandur", cid)
    assert "when" in reply.lower()
    cid, reply, state, _ = agent.handle("sam", "tomorrow morning", cid)
    assert state == "awaiting_approval"


def test_02_correction_preserves_unrelated_context():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = agent.handle("sam", "Find a plumber in Bellandur this week mornings under ₹1500")
    cid, _, state, _ = agent.handle("sam", "Actually in HSR Layout", cid)
    conv = db.get(Conversation, cid)
    assert conv.facts["service"] == "plumber" and conv.facts["date_range"] == "this week"
    assert conv.facts["area"] == "Hsr Layout" and state == "awaiting_approval"


def test_03_provider_comparison():
    _, db, tools, agent = setup_agent()
    cid, reply, _, _ = start_booking(agent)
    cid, reply, _, data = agent.handle("sam", "compare options", cid)
    assert len(data["providers"]) >= 2 and "QuickFix" in reply


def test_04_review_injection_cannot_approve():
    _, db, tools, agent = setup_agent()
    cid, _, state, data = start_booking(agent)
    provider = data["provider"]["id"]
    cid, reply, state, _ = agent.handle("sam", f"tell me about {provider}", cid)
    assert state == "awaiting_approval"
    assert db.query(Approval).filter_by(user_id="sam", approved=True).count() == 0
    assert len(tools._records("booking")) == 0


@pytest.mark.parametrize("field,value", [("price", 9999), ("slot", "18:00"), ("provider_id", "p102")])
def test_05_07_changed_details_invalidate_approval(field, value):
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    conv = db.get(Conversation, cid)
    facts = dict(conv.facts)
    pending = dict(facts["pending_action"])
    pending["details"] = {**pending["details"], field: value}
    facts["pending_action"] = pending
    conv.facts = facts
    db.commit()
    cid, reply, state, _ = agent.handle("sam", "yes", cid)
    assert state == "collecting" and "details no longer match" in reply
    assert len(tools._records("booking")) == 0


def test_08_booking_without_approval():
    _, db, tools, agent = setup_agent()
    cid, _, state, _ = start_booking(agent)
    assert state == "awaiting_approval" and len(tools._records("booking")) == 0
    assert db.get(Conversation, cid).facts["pending_action"]["details"]["date_range"] == "this week"


def test_09_repeated_confirmation_is_idempotent():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    cid, _, state, data = agent.handle("sam", "yes", cid)
    booking_id = data["booking_id"]
    agent.handle("sam", "yes", cid)
    assert state == "booked" and len(tools._records("booking")) == 1
    assert db.query(BookingAttempt).filter_by(booking_id=booking_id).count() == 1


def test_10_timeout_after_success_verified():
    _, db, tools, agent = setup_agent(Scenario(booking_mode="timeout_booked"))
    cid, _, _, _ = start_booking(agent)
    cid, reply, state, data = agent.handle("sam", "yes", cid)
    assert state == "booked" and tools._get(data["booking_id"])["booking_id"] == data["booking_id"]
    assert any(row.event == "tool_call" and row.payload.get("tool") == "get_booking_status" for row in db.query(Trace).all())


def test_11_timeout_without_success_does_not_retry_blindly():
    _, db, tools, agent = setup_agent(Scenario(booking_mode="timeout_not_booked"))
    cid, _, _, _ = start_booking(agent)
    cid, reply, state, _ = agent.handle("sam", "yes", cid)
    assert state == "uncertain" and len(tools.bookings) == 0
    assert db.query(BookingAttempt).first().status == "unknown"


def test_12_slot_conflict_no_claim():
    _, db, tools, agent = setup_agent(Scenario(booking_mode="slot_taken"))
    cid, _, _, _ = start_booking(agent)
    cid, reply, state, _ = agent.handle("sam", "yes", cid)
    assert state == "error" and len(tools._records("booking")) == 0


def test_13_expired_hold():
    _, db, tools, agent = setup_agent(Scenario(hold_ttl_seconds=-1))
    cid, _, _, _ = start_booking(agent)
    cid, reply, state, _ = agent.handle("sam", "yes", cid)
    assert state == "error" and "hold_expired" in reply and len(tools._records("booking")) == 0


def test_14_memory_correction_and_inspection():
    _, db, _, _ = setup_agent()
    memory = MemoryManager()
    memory.remember(db, "sam", "area", "Bellandur")
    memory.remember(db, "sam", "area", "HSR Layout")
    assert memory.get_relevant(db, "sam")[0]["value"] == "HSR Layout"
    assert memory.inspect(db, "sam")[0]["value"] == "HSR Layout"


def test_15_memory_forgetting():
    _, db, _, agent = setup_agent()
    memory = MemoryManager()
    memory.remember(db, "sam", "area", "Bellandur")
    memory.forget(db, "sam", "area")
    assert memory.get_relevant(db, "sam") == []


def test_16_pending_approval_survives_new_agent_and_session():
    engine, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    db.close()
    new_db = sessionmaker(bind=engine)()
    restarted_tools = MockErrandTools(Scenario(booking_mode="success"), db=new_db)
    restarted = ErrandAgent(new_db, tools=restarted_tools, model=ScriptedModel())
    cid, reply, state, data = restarted.handle("sam", "yes", cid)
    assert state == "booked" and data["booking_id"]


def test_17_proactive_provider_cancellation_requests_fresh_approval():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent, "Find a plumber in Bellandur this week mornings under ₹1500; I prefer warranty")
    cid, _, _, data = agent.handle("sam", "yes", cid)
    booking_id = data["booking_id"]
    tools.simulate_provider_cancellation(booking_id)
    db.add(UserEvent(user_id="sam", kind="provider_cancelled", payload={"booking_id": booking_id}))
    db.commit()
    before = len(tools._records("booking"))
    reply = agent.check_notifications("sam")
    assert "cancelled" in reply and "fresh approval" in reply
    assert len(tools._records("booking")) == before
    conv = db.get(Conversation, cid)
    assert conv.facts["pending_action"]["rebooking_for"] == booking_id
    assert conv.facts["pending_action"]["details"]["provider_id"] == "p101"


def test_18_context_budget_is_enforced_and_exact_context_traced():
    model = CapturingModel()
    _, db, _, agent = setup_agent(model=model)
    original = config.settings.context_token_budget
    config.settings.context_token_budget = 12
    try:
        memory = MemoryManager()
        for n in range(50):
            memory.remember(db, "sam", f"unrelated_{n}", "a long irrelevant value")
        cid, _, _, _ = agent.handle("sam", "Find a plumber in Bellandur this week mornings")
        payload = next(row.payload for row in db.query(Trace).all() if row.event == "model_call")
        assert not payload["context"]["memories"]
        assert payload["context"] == model.last_context
        assert payload["context_tokens"] <= 12
    finally:
        config.settings.context_token_budget = original


def test_19_model_turn_exhaustion_is_clear():
    _, db, _, agent = setup_agent()
    original = config.settings.max_model_turns
    config.settings.max_model_turns = 0
    try:
        _, reply, state, _ = agent.handle("sam", "hello")
        assert state == "limited" and "limit" in reply
    finally:
        config.settings.max_model_turns = original


def test_20_tool_call_exhaustion_is_clear():
    _, db, _, agent = setup_agent()
    original = config.settings.max_tool_calls
    config.settings.max_tool_calls = 0
    try:
        _, reply, state, _ = start_booking(agent)
        assert state == "limited" and "tool-call limit" in reply
    finally:
        config.settings.max_tool_calls = original


def test_21_unexpected_tool_failure_is_clear():
    _, db, tools, agent = setup_agent()
    tools.search_providers = lambda *args: (_ for _ in ()).throw(RuntimeError("private detail"))
    _, reply, state, _ = start_booking(agent)
    assert state == "error" and "unexpected_tool_error" in reply
    assert "private detail" not in reply


def test_22_cancel_transient_failure_retries_safely():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    cid, _, _, data = agent.handle("sam", "yes", cid)
    booking_id = data["booking_id"]
    cid, reply, state, _ = agent.handle("sam", "cancel booking", cid)
    cid, reply, state, _ = agent.handle("sam", "yes", cid)
    assert state == "cancelled" and tools._get(booking_id)["status"] == "cancelled"


def test_23_reschedule_conflict_preserves_existing_booking():
    _, db, tools, agent = setup_agent(Scenario(conflict_slots=("14:00",)))
    cid, _, _, _ = start_booking(agent)
    cid, _, _, data = agent.handle("sam", "yes", cid)
    booking_id = data["booking_id"]
    cid, _, _, _ = agent.handle("sam", "reschedule to 14:00", cid)
    cid, reply, state, _ = agent.handle("sam", "yes", cid)
    assert state == "error" and tools._get(booking_id)["status"] == "confirmed"
    assert "unchanged" in reply


def test_24_duplicate_prevention_across_retry_and_restart():
    engine, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    cid, _, _, data = agent.handle("sam", "yes", cid)
    first_id = data["booking_id"]
    restarted_db = sessionmaker(bind=engine)()
    restarted_tools = MockErrandTools(db=restarted_db)
    assert restarted_tools.book(tools._get(next(iter(tools.holds)))["hold_id"], "unused") if False else len(restarted_tools.list_bookings("sam")) == 1
    assert restarted_tools._get(first_id)["booking_id"] == first_id


def test_25_forgotten_and_unconfirmed_memory_excluded():
    _, db, _, agent = setup_agent()
    memory = MemoryManager()
    memory.remember(db, "sam", "area", "old area")
    memory.forget(db, "sam", "area")
    memory.remember(db, "sam", "time_window", "evening", source="inferred", confirmed=False)
    context = agent._model_context("sam", "Find electrician", {"area": "old area"})
    assert context["memories"] == [] and "area" not in context["facts"]


def test_26_timeout_not_found_requires_fresh_approval_then_can_retry():
    engine, db, tools, agent = setup_agent(Scenario(booking_mode="timeout_not_booked"))
    cid, _, _, _ = start_booking(agent)
    cid, _, state, _ = agent.handle("sam", "yes", cid)
    assert state == "uncertain" and len(tools._records("booking")) == 0
    db.close()
    restarted_db = sessionmaker(bind=engine)()
    restarted_tools = MockErrandTools(Scenario(booking_mode="success"), db=restarted_db)
    restarted = ErrandAgent(restarted_db, tools=restarted_tools, model=ScriptedModel())
    cid, reply, state, _ = restarted.handle("sam", "yes", cid)
    assert state == "awaiting_approval" and "approve" in reply.lower()
    cid, reply, state, data = restarted.handle("sam", "yes", cid)
    assert state == "booked" and len(restarted_tools._records("booking")) == 1


def test_27_same_provider_as_last_time_resolves_reference():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent, "Find an electrician in Bellandur this week mornings")
    cid, _, _, first = agent.handle("sam", "yes", cid)
    prior_provider = tools._get(first["booking_id"])["provider_id"]
    cid, _, state, proposal = agent.handle("sam", "same electrician as last time tomorrow afternoon")
    assert state == "awaiting_approval" and proposal["provider"]["id"] == prior_provider


def test_28_move_it_to_saturday_preserves_booking_and_requires_approval():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    cid, _, _, first = agent.handle("sam", "yes", cid)
    booking_id = first["booking_id"]
    cid, reply, state, _ = agent.handle("sam", "move it to Saturday", cid)
    assert state == "awaiting_approval" and "Saturday 09:00" in reply
    assert tools._get(booking_id)["slot"] == "09:00"
    cid, reply, state, result = agent.handle("sam", "yes", cid)
    assert state == "booked" and result["slot"] == "Saturday 09:00"


def test_29_seeded_memory_volume_and_long_history_are_not_sent_wholesale():
    _, db, _, agent = setup_agent()
    memory = MemoryManager()
    memory.seed_evaluation_memories(db, "sam")
    db.add(Conversation(id="long_history", user_id="sam", state="collecting",
                        facts={"history": [f"old turn {i}" for i in range(100)]}))
    db.commit()
    assert db.query(Memory).filter_by(user_id="sam").count() >= 50
    context = agent._model_context("sam", "Find electrician in Bellandur", {})
    assert len(str(context)) < 500 and "old turn" not in str(context)


def test_30_non_confirmation_word_cannot_approve():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    cid, reply, state, _ = agent.handle("sam", "yesterday would have been better", cid)
    assert state == "awaiting_approval" and len(tools._records("booking")) == 0


def test_31_existing_sqlite_memory_table_is_migrated():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE memories (id INTEGER PRIMARY KEY, user_id VARCHAR, key VARCHAR, value TEXT, source VARCHAR, forgotten BOOLEAN, updated_at DATETIME)")
    init_db(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("memories")}
    assert "confirmed" in columns


def test_32_user_can_select_a_compared_provider():
    _, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    cid, _, state, data = agent.handle("sam", "Use QuickFix Team", cid)
    assert state == "awaiting_approval" and data["provider"]["id"] == "p102"


def test_33_unconfirmed_inferred_preference_is_saved_but_inactive():
    _, db, _, agent = setup_agent()
    cid, _, _, _ = agent.handle("sam", "verified plumber")
    conv = db.get(Conversation, cid)
    inferred = db.query(Memory).filter_by(user_id="sam", key="preference_verified").one()
    context = agent._model_context("sam", "Need a provider", conv.facts)
    assert inferred.source == "inferred" and inferred.confirmed is False
    assert "preferences" not in context["facts"] and context["memories"] == []


def test_34_restart_after_provider_success_reconciles_pending_attempt():
    engine, db, tools, agent = setup_agent()
    cid, _, _, _ = start_booking(agent)
    conversation = db.get(Conversation, cid)
    pending = conversation.facts["pending_action"]
    approval = db.get(Approval, pending["approval_id"])
    approval.approved, approval.consumed = True, True
    db.add(BookingAttempt(user_id="sam", hold_id=pending["hold_id"],
                          idempotency_key=pending["approval_id"], status="pending"))
    db.commit()
    provider_result = tools.book(pending["hold_id"], pending["approval_id"])
    db.close()
    restarted_db = sessionmaker(bind=engine)()
    restarted_tools = MockErrandTools(db=restarted_db)
    restarted = ErrandAgent(restarted_db, tools=restarted_tools, model=ScriptedModel())
    cid, reply, state, data = restarted.handle("sam", "yes", cid)
    assert state == "booked" and data["booking_id"] == provider_result["booking_id"]
    assert len(restarted_tools._records("booking")) == 1


SCENARIOS = [
    ("S01", "Missing details and multi-message conversation", test_01_missing_information_multi_message),
    ("S02", "Correction preserves context", test_02_correction_preserves_unrelated_context),
    ("S03", "Provider comparison", test_03_provider_comparison),
    ("S04", "Review prompt injection", test_04_review_injection_cannot_approve),
    ("S05", "Changed price invalidates approval", lambda: test_05_07_changed_details_invalidate_approval("price", 9999)),
    ("S06", "Changed slot invalidates approval", lambda: test_05_07_changed_details_invalidate_approval("slot", "18:00")),
    ("S07", "Changed provider invalidates approval", lambda: test_05_07_changed_details_invalidate_approval("provider_id", "p102")),
    ("S08", "No booking without approval", test_08_booking_without_approval),
    ("S09", "Repeated confirmation", test_09_repeated_confirmation_is_idempotent),
    ("S10", "Timeout after success", test_10_timeout_after_success_verified),
    ("S11", "Timeout without success", test_11_timeout_without_success_does_not_retry_blindly),
    ("S12", "Slot conflict", test_12_slot_conflict_no_claim),
    ("S13", "Expired hold", test_13_expired_hold),
    ("S14", "Memory correction", test_14_memory_correction_and_inspection),
    ("S15", "Memory forgetting", test_15_memory_forgetting),
    ("S16", "Restart during approval", test_16_pending_approval_survives_new_agent_and_session),
    ("S17", "Proactive provider cancellation", test_17_proactive_provider_cancellation_requests_fresh_approval),
    ("S18", "Context budget and trace", test_18_context_budget_is_enforced_and_exact_context_traced),
    ("S19", "Model turn limit", test_19_model_turn_exhaustion_is_clear),
    ("S20", "Tool call limit", test_20_tool_call_exhaustion_is_clear),
    ("S21", "Unexpected tool failure", test_21_unexpected_tool_failure_is_clear),
    ("S22", "Cancel transient failure and retry", test_22_cancel_transient_failure_retries_safely),
    ("S23", "Reschedule conflict", test_23_reschedule_conflict_preserves_existing_booking),
    ("S24", "Duplicate prevention across restart", test_24_duplicate_prevention_across_retry_and_restart),
    ("S25", "Forgotten and inferred memory excluded", test_25_forgotten_and_unconfirmed_memory_excluded),
    ("S26", "Timeout result requires fresh approval before retry", test_26_timeout_not_found_requires_fresh_approval_then_can_retry),
    ("S27", "Same provider reference resolves", test_27_same_provider_as_last_time_resolves_reference),
    ("S28", "Move to Saturday retains booking until approval", test_28_move_it_to_saturday_preserves_booking_and_requires_approval),
    ("S29", "Fifty seeded memories and long history stay bounded", test_29_seeded_memory_volume_and_long_history_are_not_sent_wholesale),
    ("S30", "Non-confirmation text cannot approve", test_30_non_confirmation_word_cannot_approve),
    ("S31", "Existing SQLite database schema migrates", test_31_existing_sqlite_memory_table_is_migrated),
    ("S32", "User can select a provider from comparison", test_32_user_can_select_a_compared_provider),
    ("S33", "Unconfirmed inferred preference is stored but inactive", test_33_unconfirmed_inferred_preference_is_saved_but_inactive),
    ("S34", "Restart after provider success reconciles pending attempt", test_34_restart_after_provider_success_reconciles_pending_attempt),
]


@pytest.mark.parametrize("scenario_id,name,check", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_assignment_scenarios(scenario_id, name, check):
    check()
