import time
import uuid
from app.models import Trace


class RunTrace:
    def __init__(self, db, run_id=None):
        self.db = db
        self.run_id = run_id or "run_" + uuid.uuid4().hex[:10]
        self.started = time.perf_counter()

    def add(self, event, payload=None):
        self.db.add(Trace(run_id=self.run_id, event=event, payload=payload or {}))
        self.db.commit()

    def tool(self, name, args, result=None, error=None, latency_ms=None):
        self.add("tool_call", {
            "tool": name, "arguments": args,
            "result": result, "error": error,
            "latency_ms": latency_ms if latency_ms is not None else round((time.perf_counter() - self.started) * 1000, 2)
        })
