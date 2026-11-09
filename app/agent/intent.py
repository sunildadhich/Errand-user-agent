import json
import re


def simple_extract(message: str) -> dict:
    """Small fallback extractor; the local model adapter can provide richer extraction."""
    text = message.lower()
    result = {}
    services = ("plumber", "plumbing", "electrician", "electrical", "ac servicing", "ac service", "cleaner", "cleaning")
    for service in services:
        if service in text:
            result["service"] = service.replace("plumbing", "plumber").replace("electrical", "electrician").replace("cleaning", "cleaner")
            break

    area_match = re.search(r"(?:in|around|near)\s+([a-z][a-z\s-]{2,30}?)(?=\s+(?:this|next|tomorrow|under|before|after|on|for)\b|[,.;]|$)", text)
    if area_match:
        result["area"] = area_match.group(1).strip().title()

    if "morning" in text:
        result["time_window"] = "morning"
    elif "afternoon" in text:
        result["time_window"] = "afternoon"
    elif "evening" in text:
        result["time_window"] = "evening"
    elif "not before 10" in text or "after 10" in text:
        result["time_window"] = "10:00 or later"

    if "this week" in text:
        result["date_range"] = "this week"
    elif "tomorrow" in text:
        result["date_range"] = "tomorrow"
    elif "saturday" in text:
        result["date_range"] = "Saturday"
    elif "sunday" in text:
        result["date_range"] = "Sunday"
    elif "today" in text:
        result["date_range"] = "today"
    elif "next week" in text:
        result["date_range"] = "next week"

    for preference in ("verified", "warranty", "same-day", "lowest price", "highly rated"):
        if preference in text:
            result.setdefault("preferences", []).append(preference)

    provider_match = re.search(r"\b(?:provider\s+)?(p\d{3}|e\d{3}|a\d{3}|c\d{3})\b", text)
    if provider_match:
        result["provider_id"] = provider_match.group(1)

    slot_match = re.search(r"\b(?:at\s+)?(\d{1,2}:\d{2})\b", text)
    if slot_match:
        result["time_window"] = slot_match.group(1).zfill(5)

    budget_match = re.search(r"(?:under|below|budget(?: of)?)\s*₹?\s*([0-9,]+)", text)
    if budget_match:
        result["budget"] = int(budget_match.group(1).replace(",", ""))

    return result


def merge_facts(existing: dict, new: dict) -> dict:
    merged = dict(existing or {})
    for key, value in new.items():
        if value is not None and value != "":
            merged[key] = value
    return merged
