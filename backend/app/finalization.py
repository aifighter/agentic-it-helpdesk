from __future__ import annotations

import re
from typing import Any

from .action_schemas import AskUserAction, EscalateAction, FinalAnswerAction
from .evidence_guards import has_unread_kb_match
from .escalation_reply import compose_escalation_reply
from .handoff_executor import create_handoff_for_escalation
from .manifest_matching import conversation_text, matched_policy_actions, matched_services, needs_change_log
from .observations import append_step, plain_text, tool_observation
from .risk_guardrails import has_unmodeled_high_risk_text, state_high_risk_text
from .schemas import Observation
from .state import SessionState
from .tools import GenericRuntime


def validate_final_answer(state: SessionState, action: FinalAnswerAction, manifest: dict[str, Any]) -> str | None:
    if action.outcome == "acknowledged":
        if action.evidence_ids or action.policy_evidence_ids:
            return "final_answer rejected: acknowledged responses must not cite case evidence or policy evidence."
        text = f"{action.proposed_action} {action.answer}"
        if any(re.search(pattern, text, flags=re.I) for pattern in manifest.get("risk_guardrails", {}).get("high_risk_terms", [])):
            return "final_answer rejected: acknowledged response contains configured high-risk access/change semantics."
        return None
    if action.outcome == "needs_info":
        if appears_out_of_domain(state, manifest):
            if not has_boundary_language(action.answer):
                return (
                    "final_answer rejected: needs_info response appears outside connected domain sources. "
                    "Regenerate with a natural boundary statement that the agent is not connected to a dedicated KB/status API/admin tool for this system."
                )
            if asks_for_troubleshooting_details(action.answer):
                return (
                    "final_answer rejected: unsupported systems should not ask for more troubleshooting details that the agent cannot use. "
                    "Regenerate a concise boundary answer, or escalate to general IT triage with available context."
                )
        return None
    evidence_ids = set(action.evidence_ids)
    policy_ids = set(action.policy_evidence_ids)
    if not evidence_ids:
        return "final_answer rejected: missing non-policy evidence_ids."
    if not policy_ids:
        return "final_answer rejected: missing policy_evidence_ids."
    observations = {obs.id: obs for obs in state.observations}
    if not all(obs_id in observations for obs_id in evidence_ids | policy_ids):
        return "final_answer rejected: referenced observation id does not exist."
    if not any(observations[obs_id].type != "user_request" for obs_id in evidence_ids):
        return "final_answer rejected: resolved answers require non-policy evidence beyond the current user request."
    if has_unread_kb_match(state, evidence_ids):
        return "final_answer rejected: file_tool.grep found a KB match, but planner has not read the matched KB article. Use file_tool.read before final_answer."
    policy_obs = [observations[obs_id] for obs_id in policy_ids]
    if not any(obs.type == "policy_result" and obs.data.get("allowed") is True for obs in policy_obs):
        return "final_answer rejected: no policy observation allows proposed_action."
    if not cited_policy_supports_action(policy_obs, action.proposed_action):
        return "final_answer rejected: proposed_action is not supported by cited allowed policy evidence."
    if cites_low_relevance_policy(state, policy_ids):
        return "final_answer rejected: cited policy evidence followed a low-relevance runtime warning and cannot support resolved output."
    return None


def validate_ask_user(state: SessionState, action: AskUserAction, manifest: dict[str, Any]) -> str | None:
    if has_unmodeled_high_risk_text(manifest, state_high_risk_text(state)) and asks_for_troubleshooting_details(action.question):
        return (
            "ask_user rejected: current request has configured unmodeled high-risk access/change semantics. "
            "Do not ask troubleshooting fields. Escalate with policy_gap / unmodeled_high_risk_request, or ask only for approval/business context."
        )
    if not appears_out_of_domain(state, manifest):
        return None
    return (
        "ask_user rejected: user issue appears outside connected domain sources. "
        "Do not ask for more troubleshooting details that this agent cannot use. "
        "Regenerate as an escalation to general IT triage with available context, or final_answer that the agent cannot resolve this unsupported system."
    )


def appears_out_of_domain(state: SessionState, manifest: dict[str, Any]) -> bool:
    user_text = conversation_text(state.messages[-4:])
    hints = manifest.get("planner_hints", {})
    manifest_match = (
        strong_kb_topic_match(user_text, manifest.get("knowledge_base_topics", []))
        or bool(matched_policy_actions(user_text, hints))
        or needs_change_log(user_text, hints)
    )
    if manifest_match:
        return False
    low_relevance_warning = any(obs.type == "runtime_warning" and "low relevance" in obs.summary for obs in state.observations)
    has_domain_evidence = any(obs.evidence and obs.tool in {"file_tool", "http_tool", "policy_tool"} for obs in state.observations)
    return low_relevance_warning or not has_domain_evidence


def strong_kb_topic_match(text: str, topics: list[dict[str, Any]]) -> bool:
    lower = text.lower()
    for topic in topics:
        matched = [keyword.lower() for keyword in topic.get("keywords", []) if keyword.lower() in lower]
        if len(matched) >= 2:
            return True
        path = str(topic.get("path", "")).lower()
        if any(keyword in path for keyword in matched):
            return True
    return False


def has_boundary_language(text: str) -> bool:
    lower = text.lower()
    boundary_terms = ["未接入", "没有接入", "没有专门", "无法可靠", "不能可靠", "暂时无法", "not connected", "not integrated"]
    source_terms = ["知识库", "runbook", "状态", "接口", "管理", "工具", "api"]
    return any(term in lower for term in boundary_terms) and any(term in lower for term in source_terms)


def asks_for_troubleshooting_details(text: str) -> bool:
    lower = text.lower()
    patterns = [
        "请补充",
        "请提供",
        "提供以下",
        "补充以下",
        "please provide",
        "provide the",
        "send the",
        "what version",
        "which version",
        "错误信息",
        "开始时间",
        "影响范围",
        "紧急程度",
    ]
    return any(pattern in lower for pattern in patterns)


def cites_low_relevance_policy(state: SessionState, policy_ids: set[str]) -> bool:
    observations = state.observations
    for index, obs in enumerate(observations):
        if obs.id not in policy_ids or obs.type != "policy_result":
            continue
        previous = observations[index - 1] if index else None
        if previous and previous.type == "runtime_warning" and "policy_tool.evaluate action appears low relevance" in previous.summary:
            return True
    return False


def cited_policy_supports_action(policy_obs: list[Observation], proposed_action: str) -> bool:
    normalized = str(proposed_action or "").strip().lower()
    if not normalized:
        return False
    return any(
        obs.type == "policy_result"
        and obs.data.get("allowed") is True
        and str(obs.data.get("action") or "").strip().lower() == normalized
        for obs in policy_obs
    )


def build_ask_user(state: SessionState, action: AskUserAction) -> dict[str, Any]:
    append_step(state, action_type="ask_user", thought_summary=action.thought_summary, status="ok")
    return {
        "reply": plain_text(action.question),
        "outcome": "needs_info",
        "confidence": 0.62,
        "decision_rationale": "当前证据不足，必须先向用户补充询问。",
    }


def build_final_answer(state: SessionState, action: FinalAnswerAction) -> dict[str, Any]:
    append_step(state, action_type="final_answer", thought_summary=action.thought_summary, status="ok")
    return {
        "reply": plain_text(action.answer),
        "outcome": action.outcome,
        "confidence": float(action.confidence),
        "decision_rationale": plain_text(action.decision_rationale),
        "evidence_ids": list(action.evidence_ids),
        "policy_evidence_ids": list(action.policy_evidence_ids),
    }


def build_escalation(state: SessionState, runtime: GenericRuntime, action: EscalateAction, next_id) -> dict[str, Any]:
    payload = dict(action.handoff_payload)
    if action.requested_actions:
        payload.setdefault("requested_actions", list(action.requested_actions))
    if action.unmodeled_actions:
        payload.setdefault("unmodeled_actions", [item.model_dump(mode="json") for item in action.unmodeled_actions])
    if action.risk_assessment.model_dump(exclude_defaults=True):
        payload.setdefault("risk_assessment", action.risk_assessment.model_dump(mode="json"))
    payload.setdefault("title", action.title)
    payload.setdefault("team", action.team)
    payload.setdefault("reason", action.reason)
    payload.setdefault("conversation_summary", state.messages[-8:])
    payload.setdefault("tools_checked", [f"{obs.tool}.{obs.operation}" for obs in state.observations if obs.tool])
    handoff_result = create_handoff_for_escalation(runtime, payload)
    obs = tool_observation(next_id(), handoff_result, observation_type="handoff")
    state.observations.append(obs)
    append_step(
        state,
        action_type="escalate",
        thought_summary=action.thought_summary,
        status="ok",
        tool="handoff_executor",
        operation="create_handoff",
        observation_id=obs.id,
    )
    reply = compose_escalation_reply(action.team, payload, state)
    return {
        "reply": reply,
        "outcome": "escalated",
        "confidence": float(action.confidence),
        "decision_rationale": plain_text(action.reason),
        "escalation": payload,
        "evidence_ids": list(action.evidence_ids),
        "policy_evidence_ids": list(action.policy_evidence_ids),
    }


def enrich_escalation_action(state: SessionState, action: EscalateAction, manifest: dict[str, Any]) -> EscalateAction:
    return action
