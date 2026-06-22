from __future__ import annotations

from typing import Any

from .schemas import EscalateAction
from .state import SessionState
from .tools import GenericRuntime, ToolResult


def validate_escalation(state: SessionState, action: EscalateAction, manifest: dict[str, Any]) -> str | None:
    evidence_ids = set(action.evidence_ids)
    policy_ids = set(action.policy_evidence_ids)
    observations = {obs.id: obs for obs in state.observations}
    requested_actions = escalation_requested_actions(action)
    unmodeled_actions = escalation_unmodeled_actions(action)
    policy_gap_escalation = has_policy_gap_marker(action.model_dump()) or bool(
        action.risk_assessment.policy_gap or action.risk_assessment.unmodeled_high_risk_request
    )

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

    if unmodeled_actions and not policy_gap_escalation:
        return "escalate rejected: unmodeled_actions requires structured policy_gap or unmodeled_high_risk_request."
    if policy_gap_escalation and not (requested_actions or unmodeled_actions):
        return (
            "escalate rejected: policy_gap / unmodeled_high_risk_request requires action mapping. "
            "Planner must set requested_actions for exact registered policy actions or unmodeled_actions for policy gaps; runtime will not infer actions from keywords."
        )
    if has_unread_kb_match(state, evidence_ids) and not policy_gap_escalation:
        return "escalate rejected: file_tool.grep found a KB match, but planner has not read the matched KB article. Use file_tool.read before escalation."
    if not policy_ids and requested_actions:
        return "escalate rejected: requested_actions requires policy_evidence_ids."
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


def has_policy_gap_marker(value: Any) -> bool:
    markers = {"policy_gap", "unmodeled_high_risk_request"}
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower()
            if key_text in markers and item is True:
                return True
            if has_policy_gap_marker(item):
                return True
        return False
    if isinstance(value, list):
        return any(has_policy_gap_marker(item) for item in value)
    if isinstance(value, str):
        text = value.lower()
        return any(marker in text for marker in markers)
    return False


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


def compose_escalation_reply(team: str, payload: dict[str, Any], state: SessionState) -> str:
    employee = state.working_state.get("employee", {})
    requested = payload.get("requested_actions") or []
    requested_text = ", ".join(requested) if requested else payload.get("summary") or "用户报告的问题"
    approvals = sorted(
        {
            approval
            for item in state.observations
            if item.type == "policy_result"
            for approval in item.data.get("required_approvals", [])
        }
    )
    approval_text = f"需要审批：{', '.join(approvals)}。" if approvals else "当前 agent 未接入足够的专门数据源或处理工具，需要人工 triage。"
    person = employee.get("name") or state.user_email or "该员工"
    business_context = state.messages[-1]["content"] if state.messages else ""
    return (
        f"这个请求不能由 agent 直接完成，我已升级给 {team}。\n\n"
        f"请求项：{requested_text.rstrip('。.!?')}。\n"
        f"员工：{person}（{state.user_email or 'unknown'}）。\n"
        f"原因：{approval_text}\n"
        f"业务背景：{business_context}\n\n"
        f"我已经把员工信息、{'policy 结果、' if approvals else ''}已查询的工具证据和对话上下文放入 handoff payload，下一位处理人可以直接接手。"
    )


def create_handoff_for_escalation(runtime: GenericRuntime, payload: dict[str, Any]) -> ToolResult:
    return runtime.handoff.create(payload)
