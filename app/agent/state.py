from dataclasses import dataclass, field


@dataclass
class ErrandState:
    service: str | None = None
    area: str | None = None
    date_range: str | None = None
    time_window: str | None = None
    budget: int | None = None
    preferences: list[str] = field(default_factory=list)
    selected_provider: dict | None = None
    selected_slot: str | None = None
    quoted_price: int | None = None
    hold_id: str | None = None
    approval_id: str | None = None
    phase: str = "collecting"

    def missing_fields(self):
        missing = []
        for key in ("service", "area", "date_range", "time_window"):
            if not getattr(self, key):
                missing.append(key)
        return missing
