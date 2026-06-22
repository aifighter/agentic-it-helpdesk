from __future__ import annotations

from typing import Any

from .escalation import compose_escalation_reply, create_handoff_for_escalation, has_unread_kb_match
from .observations import append_step, plain_text, tool_observation
from .schemas import AskUserAction, EscalateAction, FinalAnswerAction, Observation
from .state import SessionState
from .tools import GenericRuntime


def validate_final_answer(state: SessionState, action: FinalAnswerAction, manifest: dict[str, Any]) -> str | None:
    if action.outcome == "acknowledged":
        if action.evidence_ids or action.policy_evidence_ids:
            return "final_answer rejected: acknowledged responses must not cite case evidence or policy evidence."
        return None
    if action.outcome == "unsupported":
        if action.evidence_ids or action.policy_evidence_ids:
            return "final_answer rejected: unsupported responses must not cite case evidence or policy evidence."
        return None
    if action.outcome == "needs_info":
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
    return None


def validate_ask_user(state: SessionState, action: AskUserAction, manifest: dict[str, Any]) -> str | None:
    if terminal_escalation_required(state):
        return "ask_user rejected: compliance checker already required escalation for the current terminal draft."
    return None


def terminal_escalation_required(state: SessionState) -> bool:
    return any(
        obs.type == "planner_note"
        and isinstance(obs.data.get("compliance"), dict)
        and obs.data["compliance"].get("required_next_action") == "escalate"
        for obs in state.observations[-8:]
    )


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
