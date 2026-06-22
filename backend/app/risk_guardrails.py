from __future__ import annotations

import json
import re
from typing import Any

from .manifest_matching import conversation_text
from .state import SessionState

POLICY_GAP_MARKERS = {"policy_gap", "unmodeled_high_risk_request"}


def configured_high_risk_terms(manifest: dict[str, Any]) -> list[str]:
    guardrails = manifest.get("risk_guardrails", {})
    terms = list(guardrails.get("high_risk_terms", []))
    terms.extend(unmodeled_high_risk_config(manifest).get("terms", []))
    return terms


def unmodeled_high_risk_config(manifest: dict[str, Any]) -> dict[str, Any]:
    return manifest.get("risk_guardrails", {}).get("unmodeled_high_risk_escalation", {})


def has_configured_high_risk_text(manifest: dict[str, Any], text: str) -> bool:
    return any(re.search(pattern, text, flags=re.I) for pattern in configured_high_risk_terms(manifest))


def has_unmodeled_high_risk_text(manifest: dict[str, Any], text: str) -> bool:
    config = unmodeled_high_risk_config(manifest)
    if not config.get("enabled"):
        return False
    return any(re.search(pattern, text, flags=re.I) for pattern in config.get("terms", []))


def has_policy_gap_marker(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in POLICY_GAP_MARKERS and item is True:
                return True
            if has_policy_gap_marker(item):
                return True
        return False
    if isinstance(value, list):
        return any(has_policy_gap_marker(item) for item in value)
    if isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in POLICY_GAP_MARKERS)
    return False


def state_high_risk_text(state: SessionState) -> str:
    observation_text = " ".join(obs.summary for obs in state.observations[-12:] if obs.visible)
    return f"{conversation_text(state.messages)} {observation_text}"


def is_unmodeled_high_risk_escalation(manifest: dict[str, Any], state: SessionState, draft: dict[str, Any]) -> bool:
    text = f"{state_high_risk_text(state)} {json.dumps(draft, ensure_ascii=False, default=str)}"
    return has_policy_gap_marker(draft) and has_unmodeled_high_risk_text(manifest, text)
