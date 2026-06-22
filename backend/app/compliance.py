from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .escalation import has_policy_gap_marker
from .llm import DeepSeekClient
from .observations import append_step, tool_calls_from_observations
from .schemas import ComplianceResult, Observation, ToolCall
from .state import SessionState
from .tools import summarize


@dataclass
class ComplianceChecker:
    llm: DeepSeekClient
    policy_rules: dict[str, Any]

    def check(
        self,
        *,
        draft_action_type: str,
        draft: dict[str, Any],
        user_messages: list[dict[str, str]],
        observations: list[Observation],
        tool_calls: list[ToolCall],
    ) -> dict[str, Any]:
        deterministic = deterministic_compliance_check(
            draft_action_type=draft_action_type,
            draft=draft,
            user_messages=user_messages,
            observations=observations,
        )
        if deterministic:
            return deterministic
        payload = {
            "draft_action_type": draft_action_type,
            "draft": draft,
            "conversation": user_messages[-8:],
            "observations": [obs.model_dump(mode="json") for obs in observations[-20:]],
            "tool_trace": [call.model_dump(mode="json") for call in tool_calls[-20:]],
            "policy_rules": self.policy_rules,
        }
        result = self.llm.complete_json(compliance_system_prompt(), payload)
        return normalize_checker_result(result)


def deterministic_compliance_check(
    *,
    draft_action_type: str,
    draft: dict[str, Any],
    observations: list[Observation],
    user_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    has_policy_evidence = any(obs.type == "policy_result" for obs in observations)
    has_allowed_policy = any(obs.type == "policy_result" and obs.data.get("allowed") is True for obs in observations)
    has_denied_policy = any(obs.type == "policy_result" and obs.data.get("allowed") is False for obs in observations)
    policy_actions = {obs.data.get("action") for obs in observations if obs.type == "policy_result"}
    handoff_payload = draft.get("handoff_payload") or {}
    requested_actions = set((draft.get("requested_actions") or []) + (handoff_payload.get("requested_actions") or []))
    unmodeled_actions = (draft.get("unmodeled_actions") or []) + (handoff_payload.get("unmodeled_actions") or [])
    policy_gap = has_policy_gap_marker(draft)

    if draft_action_type == "final_answer" and draft.get("outcome") == "acknowledged":
        return {
            "compliant": True,
            "risk_level": "low",
            "reason": "Acknowledgement draft does not claim operational action and does not require case evidence.",
            "required_next_action": "allow",
            "confidence": 0.97,
        }

    if draft_action_type == "escalate" and policy_gap and unmodeled_actions:
        return {
            "compliant": True,
            "risk_level": "medium",
            "reason": "Escalation handles an unmodeled high-risk request / policy gap without promising execution.",
            "required_next_action": "allow",
            "confidence": 0.94,
        }
    if draft_action_type == "escalate" and requested_actions:
        missing_policy = sorted(action for action in requested_actions if action not in policy_actions)
        if missing_policy:
            return {
                "compliant": False,
                "risk_level": "high",
                "reason": f"Escalation draft is missing policy evidence for requested actions: {', '.join(missing_policy)}.",
                "required_next_action": "reject",
                "confidence": 0.94,
            }
        if requested_actions and has_allowed_policy and not has_denied_policy:
            actions = ", ".join(str(obs.data.get("action")) for obs in observations if obs.type == "policy_result" and obs.data.get("allowed") is True)
            return {
                "compliant": False,
                "risk_level": "medium",
                "reason": f"Escalation draft has only allowed low-risk policy evidence and no denied policy or high-risk semantics. Planner should final_answer or gather stronger escalation evidence. Allowed actions: {actions}.",
                "required_next_action": "reject",
                "confidence": 0.93,
            }

    if draft_action_type == "final_answer" and draft.get("outcome") == "resolved" and (unmodeled_actions or policy_gap):
        return {
            "compliant": False,
            "risk_level": "high",
            "reason": "Resolved draft contains structured unmodeled high-risk / policy gap fields; planner must escalate instead of marking resolved.",
            "required_next_action": "escalate",
            "confidence": 0.94,
        }
    if draft_action_type == "final_answer" and draft.get("outcome") == "resolved" and not has_allowed_policy:
        return {
            "compliant": False,
            "risk_level": "medium",
            "reason": "Resolved draft lacks an allowed policy observation.",
            "required_next_action": "reject",
            "confidence": 0.92,
        }
    if draft_action_type == "final_answer" and draft.get("outcome") == "resolved" and has_allowed_policy:
        return {
            "compliant": True,
            "risk_level": "low",
            "reason": "Resolved draft has allowed policy evidence.",
            "required_next_action": "allow",
            "confidence": 0.96,
        }
    return None


def normalize_checker_result(result: dict[str, Any]) -> dict[str, Any]:
    required_next_action = result.get("required_next_action")
    if required_next_action not in {"allow", "reject", "escalate", "ask_user"}:
        required_next_action = "reject"
    risk_level = result.get("risk_level")
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "medium"
    model = ComplianceResult(
        compliant=bool(result.get("compliant")),
        risk_level=risk_level,
        reason=str(result.get("reason") or "Compliance checker did not provide a reason.")[:1000],
        required_next_action=required_next_action,
        confidence=float(result.get("confidence", 0.75)),
    )
    return model.model_dump()


def compliance_system_prompt() -> str:
    return """
你是 Mandatory Compliance Checker，不是 helpdesk planner。
你只能输出 JSON object，不要输出解释文本。
你要审核 planner 的 final_answer / escalate 草稿是否可以被 runtime 放行。

审核输入包括：user conversation、draft reply/action、observations、tool trace、policy_rules。
你必须根据 policy + evidence + draft response 的语义判断风险，不要求 proposed_action 必须来自固定枚举。

输出 JSON schema:
{
  "compliant": true | false,
  "risk_level": "low" | "medium" | "high",
  "reason": "简短说明",
  "required_next_action": "allow" | "reject" | "escalate" | "ask_user",
  "confidence": 0.0-1.0
}

规则：
- 如果 draft 承诺执行 policy_rules 中受控/高风险语义对应的动作，但缺少 policy evidence 或 policy 要求人工审批，则 compliant=false。
- 如果 draft 是 unmodeled high-risk / policy gap，final resolved 不合规；escalate 可以放行，但必须有 unmodeled_actions 或结构化 risk_assessment。
- 如果 draft 是 final resolved，必须有 allowed=true 的 policy_result 支撑。
- 如果 draft 是 escalate，必须有清晰 handoff、证据或 policy 理由；不能承诺已经执行受控动作。
- 如果 draft 缺少必要业务信息但风险不是必须升级，required_next_action="ask_user"。
- 不要泄露 hidden chain-of-thought，只给 reason。
""".strip()


def compliance_summary(result: dict[str, Any]) -> str:
    return (
        f"Compliance checker: compliant={result.get('compliant')}, "
        f"risk={result.get('risk_level')}, next={result.get('required_next_action')}. "
        f"{summarize(result.get('reason', ''), 300)}"
    )


def check_compliance(
    *,
    state: SessionState,
    checker: ComplianceChecker,
    draft_action_type: str,
    draft: dict[str, Any],
    next_id,
) -> str | None:
    observations = [obs for obs in state.observations if obs.visible][-20:]
    result = checker.check(
        draft_action_type=draft_action_type,
        draft=draft,
        user_messages=state.messages,
        observations=observations,
        tool_calls=tool_calls_from_observations(observations),
    )
    obs = Observation(
        id=next_id(),
        type="planner_note",
        ok=bool(result.get("compliant")),
        summary=compliance_summary(result),
        data={"compliance": result, "draft_action_type": draft_action_type},
        visible=True,
    )
    state.observations.append(obs)
    append_step(
        state,
        action_type="compliance_check",
        thought_summary="Mandatory compliance checker 审核最终草稿。",
        status="ok" if result.get("compliant") else "rejected",
        observation_id=obs.id,
    )
    if result.get("compliant") and result.get("required_next_action") == "allow":
        return None
    return compliance_summary(result)
