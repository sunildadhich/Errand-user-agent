from sqlalchemy.orm import Session
from app.models import Memory


class MemoryManager:
    def remember(self, db: Session, user_id: str, key: str, value: str, source="explicit", confirmed=None):
        confirmed = source == "explicit" if confirmed is None else confirmed
        row = db.query(Memory).filter_by(user_id=user_id, key=key, forgotten=False, source=source).first()
        if row:
            row.value, row.confirmed = value, confirmed
        else:
            db.add(Memory(user_id=user_id, key=key, value=value, source=source, confirmed=confirmed, forgotten=False))
        db.commit()

    def get_relevant(self, db: Session, user_id: str, keys=None):
        query = db.query(Memory).filter_by(user_id=user_id, forgotten=False)
        if keys:
            query = query.filter(Memory.key.in_(keys))
        return [{"key": m.key, "value": m.value, "source": m.source, "confirmed": m.confirmed}
                for m in query.all() if m.source == "explicit" or (m.source == "inferred" and m.confirmed)]

    def inspect(self, db: Session, user_id: str):
        return [{"key": m.key, "value": m.value, "source": m.source,
                 "confirmed": m.confirmed, "forgotten": m.forgotten}
                for m in db.query(Memory).filter_by(user_id=user_id).order_by(Memory.id).all()]

    def confirm(self, db: Session, user_id: str, key: str):
        rows = db.query(Memory).filter_by(user_id=user_id, key=key, source="inferred", forgotten=False).all()
        for row in rows:
            row.confirmed = True
        db.commit()
        return len(rows)

    def forget(self, db: Session, user_id: str, key: str):
        rows = db.query(Memory).filter_by(user_id=user_id, key=key, forgotten=False).all()
        for row in rows:
            row.forgotten = True
        db.commit()
        return len(rows)

    def seed_evaluation_memories(self, db: Session, user_id="eval-user"):
        for index in range(50):
            self.remember(db, user_id, f"preference_{index:02d}", f"evaluation preference {index:02d}")
        self.remember(db, user_id, "service", "electrician")
        self.remember(db, user_id, "provider", "e201")
        self.remember(db, user_id, "time_window", "morning", source="inferred", confirmed=False)
