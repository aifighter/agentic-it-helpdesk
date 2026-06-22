from __future__ import annotations

from typing import Any

from .action_schemas import EscalateAction
from .evidence_guards import has_unread_kb_match
from .risk_guardrails import has_unmodeled_high_risk_text, is_unmodeled_high_risk_escalation, state_high_risk_text
from .state import SessionState


def validate_escalation(state: SessionState, action: EscalateAction, manifest: dict[str, Any]) -> str | None:
    evidence_ids = set(action.evidence_ids)
    policy_ids = set(action.policy_evidence_ids)
    observations = {obs.id: obs for obs in state.observations}
    requested_actions = escalation_requested_actions(action)
    unmodeled_actions = escalation_unmodeled_actions(action)
    if not evidence_ids:
        return "escalate rejected: missing evidence_ids."
    if not all(obs_id in observations for obs_id in evidence_ids | policy_ids):
        return "escalate rejected: referenced observation id does not exist."
    if mismatch := escalation_mapping_mismatch(action):
        return mismatch
    registered_actions = registered_policy_actions(manifest)
    unknown_actions = sorted(action_id for action_id in requested_actions if action_id not in registered_actions)
    if unknown_actions:
        return f"escalate rejected: requested_actions contains unregistered policy action(s): {', '.join(unknown_actions)}."
    if requested_actions:
        if not policy_ids:
            return (
                "escalate rejected: requested_actions requires corresponding policy_evidence_ids. "
                "If matching policy_result observations already exist, regenerate escalate and cite their observation ids; "
                "otherwise call policy_tool.evaluate for each requested action."
            )
        cited_policy = [observations[obs_id] for obs_id in policy_ids if observations[obs_id].type == "policy_result"]
        covered = {str(obs.data.get("action") or "") for obs in cited_policy}
        missing_policy = sorted(action_id for action_id in requested_actions if action_id not in covered)
        if missing_policy:
            return (
                f"escalate rejected: policy_evidence_ids do not cover requested_actions: {', '.join(missing_policy)}. "
                "Cite the existing matching policy_result observation ids, or call policy_tool.evaluate for each missing requested action."
            )
    policy_gap_escalation = is_unmodeled_high_risk_escalation(manifest, state, action.model_dump())
    if unmodeled_actions and not policy_gap_escalation:
        return "escalate rejected: unmodeled_actions requires policy_gap / unmodeled_high_risk_request in reason, risk_assessment, or handoff_payload."
    if has_unmodeled_high_risk_text(manifest, f"{state_high_risk_text(state)} {action.model_dump_json()}") and not requested_actions and not unmodeled_actions:
        return (
            "escalate rejected: high-risk access/change request is missing action mapping. "
            "Planner must set requested_actions for exact registered policy actions or unmodeled_actions for policy gaps; runtime will not infer actions from keywords."
        )
    if has_unread_kb_match(state) and not policy_gap_escalation:
        return "escalate rejected: file_tool.grep found a KB match, but planner has not read the matched KB article. Use file_tool.read before escalation."
    if not policy_ids and "policy" in action.reason.lower() and not policy_gap_escalation:
        return "escalate rejected: policy-based escalation must cite policy_evidence_ids."
    if not action.handoff_payload:
        return "escalate rejected: missing handoff_payload."
    return None


def escalation_requested_actions(action: EscalateAction) -> list[str]:
    payload_actions = action.handoff_payload.get("requested_actions") or []
    return list(dict.fromkeys([*action.requested_actions, *payload_actions]))


def escalation_unmodeled_actions(action: EscalateAction) -> list[Any]:
    payload_actions = action.handoff_payload.get("unmodeled_actions") or []
    return [*action.unmodeled_actions, *payload_actions]


def escalation_mapping_mismatch(action: EscalateAction) -> str | None:
    payload_requested = action.handoff_payload.get("requested_actions")
    if payload_requested is not None and action.requested_actions and list(payload_requested) != list(action.requested_actions):
        return "escalate rejected: requested_actions must be supplied consistently at top level and in handoff_payload."
    payload_unmodeled = action.handoff_payload.get("unmodeled_actions")
    if payload_unmodeled is not None and action.unmodeled_actions:
        top_level = [item.model_dump(mode="json") for item in action.unmodeled_actions]
        if payload_unmodeled != top_level:
            return "escalate rejected: unmodeled_actions must be supplied consistently at top level and in handoff_payload, or only at top level."
    return None


def registered_policy_actions(manifest: dict[str, Any]) -> set[str]:
    return {
        str(item.get("action"))
        for item in manifest.get("planner_hints", {}).get("policy_actions", [])
        if item.get("action")
    }
