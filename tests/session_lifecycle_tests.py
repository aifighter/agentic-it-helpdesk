from __future__ import annotations

from backend.app.agent import HelpdeskAgent
from backend.app.config import get_manifest
from backend.app.finalization import validate_final_answer
from backend.app.observations import user_request_observation
from backend.app.schemas import AgentStep, Observation, parse_agent_action
from backend.app.state import SessionState


def test_acknowledged_final_answer_contract() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "I understand, thanks.")
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "acknowledged",
            "proposed_action": "none",
            "answer": "不客气。后续如果还有 IT 问题，可以继续描述系统和现象。",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "The current turn does not require diagnostic tools.",
            "confidence": 0.9,
            "thought_summary": "No case work needed.",
        }
    )
    assert validate_final_answer(state, action, manifest) is None

    old_evidence = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "acknowledged",
            "proposed_action": "none",
            "answer": "不客气。",
            "evidence_ids": ["obs_old"],
            "policy_evidence_ids": [],
            "decision_rationale": "Incorrectly cites old evidence.",
            "confidence": 0.9,
            "thought_summary": "Invalid acknowledgement.",
        }
    )
    rejection = validate_final_answer(state, old_evidence, manifest)
    assert rejection and "must not cite" in rejection


def test_case_state_lifecycle_contract() -> None:
    agent = HelpdeskAgent()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(Observation(id="obs_case", type="tool_result", ok=True, summary="Case evidence."))
    state.working_state["employee"] = {"email": "alex.chen@company.test"}
    state.steps.append(AgentStep(step=1, action_type="tool_call", thought_summary="Existing case step.", status="ok", tool="sql_tool", operation="query"))
    response = agent._response(
        state,
        start_index=0,
        observation_start_index=1,
        final_payload={
            "reply": "不客气。",
            "outcome": "acknowledged",
            "confidence": 0.9,
            "decision_rationale": "No case work needed.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
        },
    )
    assert response.outcome == "acknowledged"
    assert response.evidence == []
    assert response.observations == []

    state.clear_case_context()
    assert state.observations == []
    assert state.steps == []
    assert state.working_state == {}


def test_acknowledged_response_hides_current_user_request_observation() -> None:
    agent = HelpdeskAgent()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(user_request_observation("obs_user_request", "你好", state.user_email))
    response = agent._response(
        state,
        start_index=0,
        observation_start_index=0,
        final_payload={
            "reply": "你好。",
            "outcome": "acknowledged",
            "confidence": 0.9,
            "decision_rationale": "No case work needed.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
        },
    )
    assert response.observations == []
    assert response.evidence == []


def test_unsupported_response_is_terminal_case_outcome() -> None:
    agent = HelpdeskAgent()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(Observation(id="obs_case", type="tool_result", ok=True, summary="Old case evidence."))
    state.working_state["employee"] = {"email": "alex.chen@company.test"}
    response = agent._response(
        state,
        start_index=0,
        observation_start_index=1,
        final_payload={
            "reply": "抱歉，我目前暂时无法支持这个系统。",
            "outcome": "unsupported",
            "confidence": 0.7,
            "decision_rationale": "Unsupported system.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
        },
    )
    assert response.outcome == "unsupported"
    assert response.evidence == []


def test_resolved_final_answer_requires_evidence_beyond_user_request() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(user_request_observation("obs_user_request", "我的 VPN 断开。", state.user_email))
    state.observations.append(
        Observation(
            id="obs_policy",
            type="policy_result",
            ok=True,
            summary="Allowed.",
            data={"allowed": True, "action": "vpn_troubleshooting"},
        )
    )
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "resolved",
            "proposed_action": "vpn_troubleshooting",
            "answer": "Use backup gateway.",
            "evidence_ids": ["obs_user_request"],
            "policy_evidence_ids": ["obs_policy"],
            "decision_rationale": "Only user request plus policy.",
            "confidence": 0.8,
            "thought_summary": "Invalid resolved answer.",
        }
    )
    rejection = validate_final_answer(state, action, manifest)
    assert rejection and "beyond the current user request" in rejection


def test_max_steps_structured_escalation_signal_escalates() -> None:
    agent = HelpdeskAgent()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "需要人工处理的请求。")
    state.working_state["last_escalation_signal"] = {
        "title": "Unmodeled access request",
        "team": "Access Review",
        "reason": "policy_gap / unmodeled_high_risk_request.",
        "requested_actions": [],
        "unmodeled_actions": [
            {
                "description": "Planner reported an unmodeled privileged access request.",
                "system": None,
                "risk_type": "privileged_access",
                "reason": "No registered policy action covers the requested privilege level.",
            }
        ],
        "risk_assessment": {
            "risk_level": "high",
            "risk_type": "privileged_access",
            "policy_gap": True,
            "unmodeled_high_risk_request": True,
        },
        "handoff_payload": {"policy_gap": True},
    }
    payload = agent._max_steps_payload(state)
    assert payload["outcome"] == "escalated"
    assert payload["escalation"]["risk_assessment"]["policy_gap"] is True
    assert "错误信息" not in payload["reply"]


def test_rejected_terminal_actions_remain_visible_in_timeline() -> None:
    agent = HelpdeskAgent()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    final_action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "resolved",
            "proposed_action": "vpn_troubleshooting",
            "answer": "Done.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "Missing evidence.",
            "confidence": 0.7,
            "thought_summary": "Planner tried final_answer.",
        }
    )
    assert agent._try_final_answer(state, final_action) is None
    assert state.steps[-2].action_type == "final_answer"
    assert state.steps[-2].status == "rejected"
    assert state.steps[-2].thought_summary == "Planner tried final_answer."
    assert state.steps[-1].action_type == "runtime_rejection"
    assert state.steps[-1].status == "rejected"

    escalation_action = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Missing evidence escalation",
            "team": "IT Helpdesk",
            "reason": "Need human help.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "handoff_payload": {"summary": "Missing evidence."},
            "confidence": 0.7,
            "thought_summary": "Planner tried escalate.",
        }
    )
    assert agent._try_escalation(state, escalation_action) is None
    assert state.steps[-2].action_type == "escalate"
    assert state.steps[-2].status == "rejected"
    assert state.steps[-1].action_type == "runtime_rejection"

    agent._record_action_rejection(state, "ask_user", "Planner tried ask_user.", "ask_user rejected: test.", "Regenerate.")
    assert state.steps[-2].action_type == "ask_user"
    assert state.steps[-2].status == "rejected"
    assert state.steps[-1].action_type == "runtime_rejection"
    assert state.steps[-1].status == "rejected"


TESTS = [
    test_acknowledged_final_answer_contract,
    test_case_state_lifecycle_contract,
    test_acknowledged_response_hides_current_user_request_observation,
    test_unsupported_response_is_terminal_case_outcome,
    test_resolved_final_answer_requires_evidence_beyond_user_request,
    test_max_steps_structured_escalation_signal_escalates,
    test_rejected_terminal_actions_remain_visible_in_timeline,
]
