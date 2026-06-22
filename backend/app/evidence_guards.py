from __future__ import annotations

from .state import SessionState


def has_unread_kb_match(state: SessionState, evidence_ids: set[str] | None = None) -> bool:
    matched_paths = {
        item.get("path")
        for obs in state.observations
        if obs.tool == "file_tool" and obs.operation == "grep" and isinstance(obs.data.get("output"), list)
        and (evidence_ids is None or obs.id in evidence_ids)
        for item in obs.data["output"]
        if item.get("path")
    }
    if not matched_paths:
        return False
    read_paths = {
        obs.data.get("tool_calls", [{}])[0].get("input", {}).get("path")
        for obs in state.observations
        if obs.tool == "file_tool" and obs.operation == "read"
    }
    return not bool(matched_paths & read_paths)
