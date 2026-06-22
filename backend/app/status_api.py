from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT


STATUS_DIR = ROOT / "data" / "status"


def load_services() -> list[dict]:
    return json.loads((STATUS_DIR / "services.json").read_text(encoding="utf-8"))


def load_changes() -> list[dict]:
    return json.loads((STATUS_DIR / "changes.json").read_text(encoding="utf-8"))


def filter_services(service: str | None = None, region: str | None = None) -> list[dict]:
    service_lower = (service or "").lower()
    region_lower = (region or "").lower()
    results = []
    for item in load_services():
        service_match = not service_lower or service_lower in item["service"].lower()
        region_match = not region_lower or any(region_lower in reg.lower() or reg.lower() in region_lower for reg in item["regions"])
        summary_match = not region_lower or region_lower in item["summary"].lower()
        if service_match and (region_match or summary_match or not region_lower):
            results.append(item)
    return results


def filter_changes(system: str | None = None) -> list[dict]:
    system_lower = (system or "").lower()
    if not system_lower:
        return load_changes()
    return [
        item
        for item in load_changes()
        if any(system_lower in candidate.lower() for candidate in item["systems"])
    ]
