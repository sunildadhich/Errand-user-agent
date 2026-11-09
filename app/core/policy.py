import hashlib
import json
import uuid
from sqlalchemy.orm import Session
from app.models import Approval


def action_fingerprint(action: str, details: dict) -> str:
    raw = json.dumps({"action": action, "details": details}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ApprovalPolicy:
    def create(self, db: Session, user_id: str, action: str, details: dict) -> Approval:
        approval = Approval(
            id="ap_" + uuid.uuid4().hex[:12],
            user_id=user_id,
            action=action,
            details_hash=action_fingerprint(action, details),
            approved=False,
            consumed=False,
        )
        db.add(approval)
        db.commit()
        return approval

    def approve(self, db: Session, approval_id: str, user_id: str, action: str, details: dict):
        record = db.get(Approval, approval_id)
        if not record or record.user_id != user_id or record.action != action:
            return False
        if record.details_hash != action_fingerprint(action, details) or record.consumed:
            return False
        record.approved = True
        db.commit()
        return True

    def consume(self, db: Session, approval_id: str, user_id: str, action: str, details: dict):
        record = db.get(Approval, approval_id)
        if not record or not record.approved or record.consumed:
            return False
        if record.user_id != user_id or record.action != action:
            return False
        if record.details_hash != action_fingerprint(action, details):
            return False
        record.consumed = True
        db.commit()
        return True
