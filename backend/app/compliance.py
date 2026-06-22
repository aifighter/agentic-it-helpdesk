from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .llm import DeepSeekClient
from .risk_guardrails import has_policy_gap_marker
from .schemas import ComplianceResult, Observation, ToolCall
from .tools import summarize


@dataclass
class ComplianceChecker:
    llm: DeepSeekClient
    policy_rules: dict[str, Any]
    high_risk_terms: list[str]

    def check(
        self,
        *,
        draft_action_type: str,
        draft: dict[str, Any],
        user_messages: list[dict[str, str]],
        observations: list[Observation],
        tool_calls: list[ToolCall],
        llm_api_key: str | None = None,
    ) -> dict[str, Any]:
        deterministic = deterministic_compliance_check(
            draft_action_type=draft_action_type,
            draft=draft,
            user_messages=user_messages,
            observations=observations,
            high_risk_terms=self.high_risk_terms,
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
            "risk_guardrails": {"high_risk_terms": self.high_risk_terms},
        }
        result = self.llm.complete_json(compliance_system_prompt(), payload, api_key=llm_api_key)
        return normalize_checker_result(result)


def deterministic_compliance_check(
    *,
    draft_action_type: str,
    draft: dict[str, Any],
    observations: list[Observation],
    high_risk_terms: list[str],
    user_messages: list[dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    draft_text = json.dumps(draft, ensure_ascii=False).lower()
    has_policy_evidence = any(obs.type == "policy_result" for obs in observations)
    has_allowed_policy = any(obs.type == "policy_result" and obs.data.get("allowed") is True for obs in observations)
    has_denied_policy = any(obs.type == "policy_result" and obs.data.get("allowed") is False for obs in observations)
    high_risk = any(re.search(pattern, draft_text, flags=re.I) for pattern in high_risk_terms)
    policy_actions = {obs.data.get("action") for obs in observations if obs.type == "policy_result"}
    handoff_payload = draft.get("handoff_payload") or {}
    requested_actions = set((draft.get("requested_actions") or []) + (handoff_payload.get("requested_actions") or []))
    unmodeled_actions = (draft.get("unmodeled_actions") or []) + (handoff_payload.get("unmodeled_actions") or [])
    policy_by_id = {obs.id: obs for obs in observations if obs.type == "policy_result"}
    cited_policy = [policy_by_id[obs_id] for obs_id in draft.get("policy_evidence_ids", []) if obs_id in policy_by_id]

    if draft_action_type == "final_answer" and draft.get("outcome") == "acknowledged":
        if high_risk:
            return {
                "compliant": False,
                "risk_level": "high",
                "reason": "Acknowledgement draft contains configured high-risk access/change semantics.",
                "required_next_action": "reject",
                "confidence": 0.94,
            }
        return {
            "compliant": True,
            "risk_level": "low",
            "reason": "Acknowledgement draft does not claim operational action and does not require case evidence.",
            "required_next_action": "allow",
            "confidence": 0.97,
        }

    if draft_action_type == "escalate" and has_policy_gap_marker(draft) and (high_risk or unmodeled_actions):
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
    if draft_action_type == "escalate":
        claims_policy_denial = bool(
            re.search(
                r"policy[^。.!?]{0,80}(not allowed|denied|missing conditions|requires escalation|require escalation|must escalate|不支持|不允许|拒绝|缺少条件|必须升级|需要升级|人工审批)|"
                r"(not allowed|denied|missing conditions|requires escalation|require escalation|must escalate|不支持|不允许|拒绝|缺少条件|必须升级|需要升级|人工审批)[^。.!?]{0,80}policy",
                draft_text,
                flags=re.I,
            )
        )
        policy_basis = cited_policy or [obs for obs in observations if obs.type == "policy_result"]
        if policy_basis and claims_policy_denial and not has_denied_policy and any(obs.data.get("allowed") is True for obs in policy_basis):
            actions = ", ".join(str(obs.data.get("action")) for obs in policy_basis if obs.data.get("allowed") is True)
            return {
                "compliant": False,
                "risk_level": "medium",
                "reason": f"Escalation draft claims policy denial or required escalation, but available policy evidence is allowed=true and no denied policy was observed for: {actions}.",
                "required_next_action": "reject",
                "confidence": 0.95,
            }
        if has_allowed_policy and not has_denied_policy and not high_risk:
            actions = ", ".join(str(obs.data.get("action")) for obs in observations if obs.type == "policy_result" and obs.data.get("allowed") is True)
            return {
                "compliant": False,
                "risk_level": "medium",
                "reason": f"Escalation draft has only allowed low-risk policy evidence and no denied policy or high-risk semantics. Planner should final_answer or gather stronger escalation evidence. Allowed actions: {actions}.",
                "required_next_action": "reject",
                "confidence": 0.93,
            }

    if draft_action_type == "final_answer" and high_risk and not has_policy_evidence:
        return {
            "compliant": False,
            "risk_level": "high",
            "reason": "Draft final answer contains high-risk access/change semantics but has no policy evidence.",
            "required_next_action": "reject",
            "confidence": 0.96,
        }
    if draft_action_type == "final_answer" and high_risk and has_denied_policy:
        return {
            "compliant": False,
            "risk_level": "high",
            "reason": "Draft final answer appears to resolve a high-risk request while policy evidence denies or requires human approval.",
            "required_next_action": "escalate",
            "confidence": 0.95,
        }
    if draft_action_type == "final_answer" and draft.get("outcome") == "resolved" and high_risk:
        return {
            "compliant": False,
            "risk_level": "high",
            "reason": "Resolved draft contains configured high-risk semantics; planner must ask for more information or escalate instead of marking resolved.",
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
    if draft_action_type == "final_answer" and draft.get("outcome") == "resolved" and has_allowed_policy and not high_risk:
        return {
            "compliant": True,
            "risk_level": "low",
            "reason": "Resolved draft has allowed policy evidence and no configured high-risk semantics.",
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
- 如果 draft 承诺执行 risk_guardrails.high_risk_terms 或 policy_rules 中受控/高风险语义对应的动作，但缺少 policy evidence 或 policy 要求人工审批，则 compliant=false。
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
