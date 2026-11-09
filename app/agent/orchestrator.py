import re
import time
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.agent.intent import merge_facts, simple_extract
from app.agent.model import OllamaModel, ScriptedModel
from app.agent.state import ErrandState
from app.config import settings
from app.core.memory import MemoryManager
from app.core.policy import ApprovalPolicy
from app.core.tracing import RunTrace
from app.models import BookingAttempt, Conversation, Memory, UserEvent
from app.tools.mock_services import MockErrandTools, ToolError


class ErrandAgent:
    def __init__(self, db: Session, tools=None, model=None):
        self.db = db
        self.tools = tools or MockErrandTools(db=db)
        if getattr(self.tools, "db", None) is None:
            self.tools.db = db
        self.model = model or (ScriptedModel() if settings.model_provider == "scripted" else OllamaModel())
        self.approvals = ApprovalPolicy()
        self.memory = MemoryManager()
        self.run = None
        self.started = 0.0
        self.tool_calls = 0
        self.model_calls = 0

    def check_notifications(self, user_id):
        self.run = RunTrace(self.db)
        self.started, self.tool_calls, self.model_calls = time.monotonic(), 0, 0
        try:
            return self._pending_cancellation(user_id)
        except Exception as exc:
            self.run.add("unexpected_error", {"operation": "check_notifications", "error": type(exc).__name__})
            return "I couldn’t check provider updates just now. I haven’t arranged a replacement."

    def handle(self, user_id: str, message: str, conversation_id=None):
        try:
            return self._handle(user_id, message, conversation_id)
        except Exception as exc:
            if self.run:
                self.run.add("unexpected_error", {"error": type(exc).__name__})
            conversation = self.db.get(Conversation, conversation_id) if conversation_id else None
            if not conversation:
                conversation = self.db.query(Conversation).filter_by(user_id=user_id).order_by(Conversation.updated_at.desc()).first()
            cid = conversation.id if conversation else ""
            return cid, "I hit an unexpected issue while handling that. I haven't reported any unverified action as complete. Please check the latest status before trying again.", "error", {}

    def _handle(self, user_id: str, message: str, conversation_id=None):
        self.run = RunTrace(self.db)
        self.started, self.tool_calls, self.model_calls = time.monotonic(), 0, 0
        conversation = self._conversation(user_id, conversation_id)
        facts = dict(conversation.facts or {})
        self.run.add("state_loaded", {"conversation_id": conversation.id, "state": conversation.state})

        event_reply = self._pending_cancellation(user_id)
        if event_reply:
            return conversation.id, event_reply, "awaiting_approval", {}

        lower = message.lower().strip()
        if lower in {"show my memories", "what do you remember", "inspect memory"}:
            return conversation.id, str(self.memory.inspect(self.db, user_id)), conversation.state, {}
        if lower.startswith("forget "):
            key = lower.removeprefix("forget ").strip()
            count = self.memory.forget(self.db, user_id, key)
            return conversation.id, f"I forgot {key}." if count else f"I don't have an active memory for {key}.", conversation.state, {}
        if lower.startswith("remember "):
            pair = re.match(r"remember\s+([\w -]+?)\s+(?:is|=)\s+(.+)", message, re.I)
            if pair:
                self.memory.remember(self.db, user_id, pair.group(1).strip().lower().replace(" ", "_"), pair.group(2).strip())
                return conversation.id, "Got it. I’ll remember that for next time.", conversation.state, {}

        context = self._model_context(user_id, message, facts)
        if self.model_calls >= settings.max_model_turns or self._expired():
            return conversation.id, "I hit the limit for this request before I could safely continue. Please try a shorter request.", "limited", {}
        self.model_calls += 1
        model_started = time.perf_counter()
        try:
            extracted = self.model.extract(message, context)
            self.run.add("model_call", {"context": context, "input": message,
                                         "output": extracted, "token_usage": self._tokens(str(context) + message),
                                         "context_tokens": self._tokens(str(context)),
                                         "model_token_usage": getattr(self.model, "last_usage", None),
                                         "latency_ms": round((time.perf_counter() - model_started) * 1000, 2),
                                         "estimated_cost": None})
        except Exception as exc:
            self.run.add("model_error", {"error": type(exc).__name__, "context": context,
                                         "latency_ms": round((time.perf_counter() - model_started) * 1000, 2)})
            extracted = simple_extract(message)

        extracted = {**simple_extract(message), **extracted}

        if self._expired():
            return conversation.id, "I hit the time limit while interpreting that request. Please try again.", "limited", {}

        extracted = self._resolve_references(user_id, message, extracted)
        for provider in facts.get("providers", []):
            if provider.get("name", "").lower() in lower:
                extracted["provider_id"] = provider["id"]
        preferences = extracted.get("preferences", []) if isinstance(extracted.get("preferences", []), list) else []
        preferences_explicit = bool(re.search(r"\b(prefer|always want|please remember)\b", lower))
        for preference in preferences:
            self.memory.remember(self.db, user_id, f"preference_{preference.lower().replace(' ', '_')}",
                                 preference, source="explicit" if preferences_explicit else "inferred", confirmed=preferences_explicit)
        if preferences and not preferences_explicit:
            extracted.pop("preferences", None)

        facts = merge_facts(facts, extracted)
        conversation.facts = facts
        conversation.updated_at = datetime.now()
        self.db.commit()
        self.run.add("facts_updated", {"keys": sorted(facts.keys())})

        if self._is_confirmation(message) and not self._has_material_change(extracted) and facts.get("pending_action"):
            return self._confirm(user_id, conversation, facts)

        command_result = self._command(lower, facts, conversation, user_id, extracted)
        if command_result:
            return conversation.id, command_result[0], command_result[1], command_result[2]

        # A correction supersedes the previous proposal; its hash can no longer authorize an action.
        if facts.get("pending_action") and self._has_material_change(extracted):
            facts.pop("pending_action", None)
            conversation.facts = facts
            self._transition(conversation, "collecting", "details_changed")
            self.db.commit()
            self.run.add("state_transition", {"from": "awaiting_approval", "to": "collecting", "reason": "details_changed"})

        state = ErrandState(**{k: facts.get(k) for k in ("service", "area", "date_range", "time_window", "budget", "preferences") if k in facts})
        missing = state.missing_fields()
        if missing:
            question = self._question(missing[0])
            self.run.add("needs_clarification", {"missing": missing})
            self._transition(conversation, "collecting", "needs_clarification")
            self.db.commit()
            return conversation.id, question, "collecting", {}

        if self._at_tool_limit():
            return conversation.id, "I reached the tool-call limit before completing the search. Please try again.", "limited", {}
        try:
            providers = self._tool("search_providers", {"service": state.service, "area": state.area, "date_range": state.date_range},
                                   lambda: self.tools.search_providers(state.service, state.area, state.date_range))
        except ToolError as exc:
            return conversation.id, f"I couldn’t search providers ({exc.code}). Please try again shortly.", "error", {}
        if not providers:
            return conversation.id, f"I couldn’t find a {state.service} in {state.area}. Would you like to try a nearby area or another date?", "collecting", {}
        budget = state.budget
        eligible = [p for p in providers if budget is None or p["price"] <= budget]
        if not eligible:
            return conversation.id, "I found providers, but their listed prices are above your budget. Should I include higher-priced options?", "collecting", {}
        chosen_id = facts.get("provider_id")
        chosen = next((p for p in eligible if p["id"] == chosen_id), None)
        ranked = sorted(eligible, key=lambda p: (-p["rating"], p["price"]))
        chosen = chosen or ranked[0]
        slot = self._pick_slot(chosen["available_slots"], state.time_window)
        if not slot:
            return conversation.id, "I couldn’t find a slot in that time window. Would another time work?", "collecting", {}
        if self._at_tool_limit():
            return conversation.id, "I reached the tool-call limit before I could hold a slot. Please try again.", "limited", {}
        try:
            hold = self._tool("hold_slot", {"provider_id": chosen["id"], "slot": slot}, lambda: self.tools.hold_slot(chosen["id"], slot))
        except ToolError as exc:
            return conversation.id, f"I couldn’t hold that slot ({exc.code}). No booking was made.", "error", {}
        details = {"provider_id": chosen["id"], "slot": slot, "price": hold["quoted_price"], "service": state.service}
        approval = self.approvals.create(self.db, user_id, "book", details)
        facts["pending_action"] = {"type": "book", "details": details, "approval_id": approval.id,
                                   "hold_id": hold["hold_id"], "provider_name": chosen["name"]}
        facts["providers"] = [{k: p[k] for k in ("id", "name", "service", "price", "rating", "attributes")} for p in ranked]
        conversation.facts = facts
        self._transition(conversation, "awaiting_approval", "booking_approval_requested")
        self.db.commit()
        self.run.add("approval_requested", {"approval_id": approval.id, "details": details})
        comparison = "; ".join(f"{p['name']} ₹{p['price']} ({p['rating']}★)" for p in ranked)
        reply = (f"I found {len(ranked)} options: {comparison}. My top-rated fit is {chosen['name']} "
                 f"({chosen['rating']}★) at ₹{hold['quoted_price']}, {slot}. Shall I book it?")
        return conversation.id, reply, "awaiting_approval", {"providers": ranked, "provider": chosen, "slot": slot, "price": hold["quoted_price"]}

    def _command(self, lower, facts, conversation, user_id, extracted=None):
        extracted = extracted or {}
        if lower.startswith("compare") or lower == "show options":
            providers = facts.get("providers", [])
            return ("Current options: " + "; ".join(f"{p['name']} ₹{p['price']} ({p['rating']}★)" for p in providers), conversation.state, {"providers": providers}) if providers else None
        if lower.startswith(("tell me about", "details for", "describe provider")):
            provider_id = facts.get("provider_id")
            if not provider_id:
                return ("Which provider would you like details about? You can use an ID from the options.", conversation.state, {})
            details = self._tool("get_provider", {"provider_id": provider_id}, lambda: self.tools.get_provider(provider_id))
            # Reviews are shown as quoted, untrusted text and never included in model context.
            return (f"{details['name']} is rated {details['rating']}★. Attributes: {', '.join(details['attributes'])}. Reviews (unverified): “{details['reviews'][0]}”", conversation.state, details)
        if lower in {"list bookings", "show bookings", "my bookings"}:
            bookings = self.tools.list_bookings(user_id)
            self._tool_trace("list_bookings", {"user_id": user_id}, {"count": len(bookings)})
            return (str(bookings) if bookings else "You don’t have any active bookings.", conversation.state, {"bookings": bookings})
        if lower.startswith("cancel") or lower.startswith("reschedule") or lower.startswith("move it"):
            attempts = self.db.query(BookingAttempt).filter_by(user_id=user_id, status="confirmed").order_by(BookingAttempt.id.desc()).all()
            if not attempts:
                return ("I couldn’t find an active booking to change.", conversation.state, {})
            attempt = attempts[0]
            booking = self.tools._get(attempt.booking_id)
            action = "cancel" if lower.startswith("cancel") else "reschedule"
            facts["action_request"] = action
            slot = extracted.get("time_window") or extracted.get("date_range") or facts.get("time_window") or facts.get("date_range")
            if action == "reschedule" and slot in {"Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}:
                old_slot = (booking or {}).get("slot", "09:00")
                time_part = old_slot[-5:] if re.search(r"\d{2}:\d{2}$", old_slot) else "09:00"
                slot = f"{slot} {time_part}"
            details = {"booking_id": attempt.booking_id, "slot": slot} if action == "reschedule" else {"booking_id": attempt.booking_id}
            approval = self.approvals.create(self.db, user_id, action, details)
            facts["pending_action"] = {"type": action, "details": details, "approval_id": approval.id,
                                       "booking_id": attempt.booking_id, "user_id": user_id}
            conversation.facts = facts
            self._transition(conversation, "awaiting_approval", f"{action}_approval_requested")
            self.db.commit()
            return ((f"I can move the booking to {slot}. Shall I do that?" if action == "reschedule" else "Shall I cancel your booking?"), "awaiting_approval", {})
        return None

    def _confirm(self, user_id, conversation, facts):
        pending = facts.get("pending_action")
        if not pending:
            return conversation.id, "There isn’t an action waiting for approval.", conversation.state, {}
        action, details = pending["type"], pending["details"]
        attempt = None
        if action == "book":
            attempt = self.db.query(BookingAttempt).filter_by(idempotency_key=pending["approval_id"]).first()
            if attempt and attempt.status == "confirmed":
                self._transition(conversation, "booked", "booking_already_confirmed")
                return conversation.id, f"This booking is already confirmed. Reference: {attempt.booking_id}.", "booked", {"booking_id": attempt.booking_id}
            if attempt and attempt.status == "unknown":
                status = self._tool("get_booking_status", {"hold_id": pending["hold_id"], "idempotency_key": pending["approval_id"]}, lambda: self.tools.get_booking_status(pending["hold_id"], pending["approval_id"]))
                if status["status"] == "confirmed":
                    attempt.status, attempt.booking_id = "confirmed", status["booking_id"]
                    self._transition(conversation, "booked", "timeout_recovery_confirmed")
                    self.db.commit()
                    return conversation.id, f"I verified that the booking completed. Reference: {status['booking_id']}.", "booked", status
                old_hold = self.tools._get(pending["hold_id"])
                if old_hold:
                    provider_id, slot, price = old_hold["provider_id"], old_hold["slot"], old_hold["price"]
                    new_hold = self.tools.hold_slot(provider_id, slot)
                    details = {**details, "price": new_hold["quoted_price"]}
                    approval = self.approvals.create(self.db, user_id, "book", details)
                    facts["pending_action"] = {**pending, "details": details, "approval_id": approval.id,
                                                "hold_id": new_hold["hold_id"]}
                    conversation.facts = facts
                    self._transition(conversation, "awaiting_approval", "fresh_approval_after_timeout")
                    self.db.commit()
                    return conversation.id, "The provider confirms no booking was made. I’ve refreshed the hold; please approve the same details again before I retry.", "awaiting_approval", {}
                return conversation.id, "The provider reports no booking and the hold expired. Please start a fresh search before retrying.", "collecting", {}
        if not self.approvals.approve(self.db, pending["approval_id"], user_id, action, details):
            self._transition(conversation, "collecting", "approval_details_mismatch")
            return conversation.id, "Those details no longer match the pending approval. I haven’t made a change; let’s review them again.", "collecting", {}
        if not self.approvals.consume(self.db, pending["approval_id"], user_id, action, details):
            self._transition(conversation, "error", "approval_unavailable")
            return conversation.id, "I couldn’t validate that approval. No change was made.", "error", {}
        if action == "book":
            attempt = BookingAttempt(user_id=user_id, hold_id=pending["hold_id"], idempotency_key=pending["approval_id"], status="pending")
            self.db.add(attempt)
            self.db.commit()
            try:
                result = self._tool("book", {"hold_id": pending["hold_id"], "idempotency_key": pending["approval_id"]}, lambda: self.tools.book(pending["hold_id"], pending["approval_id"]))
                attempt.status, attempt.booking_id = "confirmed", result["booking_id"]
                self._transition(conversation, "booked", "booking_confirmed")
                self.db.commit()
                return conversation.id, f"You’re booked! Reference: {result['booking_id']}.", "booked", result
            except ToolError as exc:
                if exc.code.startswith("timeout"):
                    status = self._tool("get_booking_status", {"hold_id": pending["hold_id"], "idempotency_key": pending["approval_id"]}, lambda: self.tools.get_booking_status(pending["hold_id"], pending["approval_id"]))
                    if status["status"] == "confirmed":
                        attempt.status, attempt.booking_id = "confirmed", status["booking_id"]
                        self._transition(conversation, "booked", "timeout_recovery_confirmed")
                        self.db.commit()
                        return conversation.id, f"You’re booked. The provider confirmed after a delay. Reference: {status['booking_id']}.", "booked", status
                    attempt.status = "unknown"
                    self._transition(conversation, "uncertain", "booking_timeout_unresolved")
                    self.db.commit()
                    return conversation.id, "The request timed out and the provider reports no booking yet. I haven’t retried. Ask me to check its status before we proceed.", "uncertain", {}
                attempt.status = "failed"
                self._transition(conversation, "error", "booking_failed")
                self.db.commit()
                return conversation.id, f"I couldn't complete the booking ({exc.code}). No booking was confirmed. Please choose another option for a fresh approval.", "error", {}
        if action == "cancel":
            try:
                result = self._tool("cancel", {"booking_id": pending["booking_id"], "idempotency_key": pending["approval_id"]}, lambda: self.tools.cancel(pending["booking_id"], pending["approval_id"]))
            except ToolError as exc:
                if exc.code != "transient_503":
                    self._transition(conversation, "error", "cancellation_failed")
                    return conversation.id, f"I couldn’t cancel the booking ({exc.code}).", "error", {}
                result = self._tool("cancel", {"booking_id": pending["booking_id"], "idempotency_key": pending["approval_id"], "retry": True}, lambda: self.tools.cancel(pending["booking_id"], pending["approval_id"]))
            self._transition(conversation, "cancelled", "cancellation_confirmed")
            self.db.commit()
            return conversation.id, "Your booking has been cancelled.", "cancelled", result
        if action == "reschedule":
            try:
                result = self._tool("reschedule", details, lambda: self.tools.reschedule(details["booking_id"], details["slot"], pending["approval_id"]))
                self._transition(conversation, "booked", "reschedule_confirmed")
                self.db.commit()
                return conversation.id, f"Your booking is moved to {result['slot']}.", "booked", result
            except ToolError as exc:
                self._transition(conversation, "error", "reschedule_failed")
                return conversation.id, f"I couldn’t move the booking ({exc.code}); the existing booking is unchanged.", "error", {}
        return conversation.id, "I don’t recognize that pending action, so I made no changes.", "error", {}

    def _pending_cancellation(self, user_id):
        event = self.db.query(UserEvent).filter_by(user_id=user_id, kind="provider_cancelled", handled=False).order_by(UserEvent.id).first()
        if not event:
            return None
        booking_id = event.payload["booking_id"]
        attempt = self.db.query(BookingAttempt).filter_by(user_id=user_id, booking_id=booking_id).first()
        if not attempt:
            event.handled = True
            self.db.commit()
            return "A provider cancelled a booking, but I couldn’t match it to your account. No replacement was booked."
        original = self.tools._get(booking_id)
        event.handled = True
        original["status"] = "provider_cancelled"
        self.tools._put(booking_id, "booking", original)
        self.db.commit()
        conversation = self.db.query(Conversation).filter_by(user_id=user_id).order_by(Conversation.updated_at.desc()).first()
        prior_facts = (conversation.facts or {}) if conversation else {}
        service = self.tools.providers.get(original["provider_id"], {}).get("service")
        area = prior_facts.get("area", "your area")
        date_range = prior_facts.get("date_range", "the original date")
        facts = {"service": service, "area": area, "date_range": date_range, "time_window": original.get("slot")}
        options = self.tools.search_providers(service, area, date_range)
        budget = prior_facts.get("budget")
        if budget is None:
            budget_memory = next((m["value"] for m in self.memory.get_relevant(self.db, user_id, ["budget"])), None)
            try:
                budget = int(budget_memory) if budget_memory is not None else None
            except (TypeError, ValueError):
                budget = None
        preferences = [str(value).lower() for value in prior_facts.get("preferences", [])]
        preferences += [m["value"].lower() for m in self.memory.get_relevant(self.db, user_id) if m["key"].startswith("preference_")]
        replacements = [p for p in options if p["id"] != original["provider_id"] and (budget is None or p["price"] <= int(budget))]
        if replacements:
            chosen = sorted(replacements, key=lambda p: (
                -sum(1 for pref in preferences if any(pref in attr.lower() for attr in p["attributes"])),
                -p["rating"], p["price"]))[0]
            slot = self._pick_slot(chosen["available_slots"], facts["time_window"])
            if slot:
                hold = self.tools.hold_slot(chosen["id"], slot)
                details = {"provider_id": chosen["id"], "slot": slot, "price": hold["quoted_price"], "service": service}
                approval = self.approvals.create(self.db, user_id, "book", details)
                if conversation:
                    conversation.facts = {**(conversation.facts or {}), "pending_action": {
                        "type": "book", "details": details, "approval_id": approval.id,
                        "hold_id": hold["hold_id"], "provider_name": chosen["name"], "rebooking_for": booking_id}}
                    self._transition(conversation, "awaiting_approval", "replacement_approval_requested")
                    self.db.commit()
                return (f"Your provider cancelled the booking. I found {chosen['name']} for {slot} at ₹{hold['quoted_price']}. "
                        "Would you like me to book this replacement? I’ll wait for your fresh approval.")
        return "Your provider cancelled the booking. I couldn’t find a suitable replacement, so no new booking was made."

    def _model_context(self, user_id, message, facts):
        words = set(re.findall(r"[a-z0-9]+", message.lower()))
        memories = self.memory.get_relevant(self.db, user_id)
        relevant = [m for m in memories if m["key"].replace("_", " ").lower() in message.lower()
                    or any(part in words for part in (m["key"] + " " + m["value"]).lower().replace("_", " ").split() if len(part) > 3)]
        forgotten = {m.key for m in self.db.query(Memory).filter_by(user_id=user_id, forgotten=True).all()}
        active = {m.key for m in self.db.query(Memory).filter_by(user_id=user_id, forgotten=False).all()}
        forgotten -= active
        current = {k: facts[k] for k in ("service", "area", "date_range", "time_window", "budget", "preferences")
                   if k in facts and k not in forgotten and f"preference_{k}" not in forgotten}
        if isinstance(current.get("preferences"), list):
            current["preferences"] = [value for value in current["preferences"]
                                       if f"preference_{str(value).lower().replace(' ', '_')}" not in forgotten]
        budget = max(1, settings.context_token_budget)
        context = {"facts": {}, "memories": []}
        for key in ("service", "area", "date_range", "time_window", "budget", "preferences"):
            if key not in current:
                continue
            candidate = {**context, "facts": {**context["facts"], key: current[key]}}
            if self._tokens(str(candidate)) <= budget:
                context = candidate
        for memory in relevant:
            candidate = {**context, "memories": context["memories"] + [memory]}
            if self._tokens(str(candidate)) > budget:
                break
            context = candidate
        return context

    def _resolve_references(self, user_id, message, extracted):
        if not re.search(r"\b(same|last time|as before)\b", message.lower()):
            return extracted
        previous = self.db.query(Conversation).filter_by(user_id=user_id, state="booked").order_by(Conversation.updated_at.desc()).first()
        if not previous:
            return extracted
        prior_facts = previous.facts or {}
        resolved = dict(extracted)
        for key in ("service", "area", "date_range", "time_window"):
            if key not in resolved and prior_facts.get(key):
                resolved[key] = prior_facts[key]
        attempts = self.db.query(BookingAttempt).filter_by(user_id=user_id, status="confirmed").order_by(BookingAttempt.id.desc()).all()
        if attempts:
            booking = self.tools._get(attempts[0].booking_id)
            if booking:
                resolved.setdefault("provider_id", booking["provider_id"])
                provider = self.tools.providers.get(booking["provider_id"], {})
                resolved.setdefault("service", provider.get("service"))
        return resolved

    @staticmethod
    def _tokens(text):
        return max(1, int(len(text.split()) * 1.3))

    def _tool_trace(self, name, args, result):
        self.run.tool(name, args, result)

    def _transition(self, conversation, new_state, reason):
        old_state = conversation.state
        if old_state != new_state:
            conversation.state = new_state
            if self.run:
                self.run.add("state_transition", {"conversation_id": conversation.id,
                                                    "from": old_state, "to": new_state, "reason": reason})

    def _tool(self, name, args, call):
        if self._at_tool_limit():
            raise ToolError("tool_limit")
        self.tool_calls += 1
        tool_started = time.perf_counter()
        try:
            result = call()
            self.run.tool(name, args, self._sanitize(result), latency_ms=round((time.perf_counter() - tool_started) * 1000, 2))
            return result
        except ToolError as exc:
            self.run.tool(name, args, error=exc.code, latency_ms=round((time.perf_counter() - tool_started) * 1000, 2))
            raise
        except Exception as exc:
            self.run.tool(name, args, error=type(exc).__name__, latency_ms=round((time.perf_counter() - tool_started) * 1000, 2))
            raise ToolError("unexpected_tool_error") from exc

    def _at_tool_limit(self):
        return self.tool_calls >= settings.max_tool_calls or self._expired()

    def _expired(self):
        return (time.monotonic() - self.started) * 1000 > settings.max_run_seconds * 1000

    @staticmethod
    def _sanitize(value):
        if isinstance(value, dict):
            return {k: ("[redacted]" if k in {"reviews", "raw", "token"} else ErrandAgent._sanitize(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [ErrandAgent._sanitize(v) for v in value]
        return value

    @staticmethod
    def _has_material_change(extracted):
        return any(k in extracted for k in ("service", "area", "date_range", "time_window", "budget", "provider_id"))

    @staticmethod
    def _is_confirmation(message):
        return bool(re.fullmatch(r"(?:yes|approve|confirm)(?:[,.! ]+(?:please|approve|confirm))*[.! ]*", message.lower().strip())) or message.lower().strip() in {"go ahead", "book it", "do it"}

    def _conversation(self, user_id, conversation_id):
        if conversation_id:
            row = self.db.get(Conversation, conversation_id)
            if row and row.user_id == user_id:
                return row
        row = Conversation(id="c_" + uuid.uuid4().hex[:10], user_id=user_id, facts={}, state="collecting")
        self.db.add(row)
        self.db.commit()
        return row

    @staticmethod
    def _question(field):
        return {"service": "What service do you need?", "area": "Which area should I search in?",
                "date_range": "When would you like the service?", "time_window": "What time works best?"}[field]

    @staticmethod
    def _pick_slot(slots, window):
        window = (window or "").lower()
        if ":" in window:
            return next((s for s in slots if s == window), None)
        if "morning" in window:
            candidates = [s for s in slots if s < "12:00"]
        elif "afternoon" in window:
            candidates = [s for s in slots if "12:00" <= s < "17:00"]
        elif "evening" in window:
            candidates = [s for s in slots if s >= "17:00"]
        else:
            candidates = slots
        return candidates[0] if candidates else None
