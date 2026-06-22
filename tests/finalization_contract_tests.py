from __future__ import annotations

from backend.app.schemas import Observation, parse_agent_action
from backend.app.config import get_manifest
from backend.app.finalization import validate_ask_user, validate_final_answer
from backend.app.state import SessionState


def test_unrelated_policy_cannot_support_resolved() -> None:
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(
        Observation(id="obs_policy", type="policy_result", ok=True, summary="Allowed unrelated action.", data={"allowed": True, "action": "registered_action"})
    )
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "resolved",
            "proposed_action": "different_action",
            "answer": "Done.",
            "evidence_ids": ["obs_policy"],
            "policy_evidence_ids": ["obs_policy"],
            "decision_rationale": "Uses evidence.",
            "confidence": 0.8,
            "thought_summary": "test",
        }
    )
    rejection = validate_final_answer(state, action, get_manifest())
    assert rejection and "proposed_action is not supported" in rejection


def test_ask_user_rejected_when_compliance_requires_escalation() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(
        Observation(
            id="obs_compliance",
            type="planner_note",
            ok=False,
            summary="Compliance checker requires escalation.",
            data={"compliance": {"required_next_action": "escalate"}},
        )
    )
    action = parse_agent_action(
        {"action_type": "ask_user", "question": "请补充客户端、错误信息、开始时间和影响范围。", "missing_information": ["client", "error", "started_at", "scope"], "thought_summary": "unknown issue"}
    )
    rejection = validate_ask_user(state, action, manifest)
    assert rejection and "compliance checker already required escalation" in rejection

def test_ask_user_has_no_keyword_content_guard() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    action = parse_agent_action(
        {
            "action_type": "ask_user",
            "question": "请补充受影响系统、错误信息、业务影响和紧急程度。",
            "missing_information": ["affected_system", "error_message", "business_impact"],
            "thought_summary": "Invalid high-risk ask_user.",
        }
    )
    assert validate_ask_user(state, action, manifest) is None


def test_needs_info_final_has_no_keyword_boundary_guard() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "needs_info",
            "proposed_action": "collect_missing_context",
            "answer": "请补充打印机型号、错误信息、开始时间、影响范围和紧急程度。",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "More context is needed.",
            "confidence": 0.5,
            "thought_summary": "unknown issue",
        }
    )
    assert validate_final_answer(state, action, manifest) is None


def test_unsupported_final_answer_contract() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "unsupported",
            "proposed_action": "unsupported_system_boundary",
            "answer": "抱歉，我目前暂时无法支持这个系统，建议转给对应支持团队。",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "System is outside connected tools and data sources.",
            "confidence": 0.7,
            "thought_summary": "Unsupported system.",
        }
    )
    assert validate_final_answer(state, action, manifest) is None

    old_evidence = action.model_copy(update={"evidence_ids": ["obs_old"]})
    rejection = validate_final_answer(state, old_evidence, manifest)
    assert rejection and "unsupported responses must not cite" in rejection


def test_acknowledged_answer_does_not_need_boundary_language() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "User asked a non-case conversational question.")
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "acknowledged",
            "proposed_action": "none",
            "answer": "I can answer this directly without starting a helpdesk case.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "Current turn does not require case evidence.",
            "confidence": 0.8,
            "thought_summary": "No case work needed.",
        }
    )
    assert validate_final_answer(state, action, manifest) is None


TESTS = [
    test_unrelated_policy_cannot_support_resolved,
    test_ask_user_rejected_when_compliance_requires_escalation,
    test_ask_user_has_no_keyword_content_guard,
    test_needs_info_final_has_no_keyword_boundary_guard,
    test_unsupported_final_answer_contract,
    test_acknowledged_answer_does_not_need_boundary_language,
]
