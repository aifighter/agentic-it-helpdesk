from __future__ import annotations

from backend.app.action_schemas import parse_agent_action
from backend.app.config import get_manifest
from backend.app.escalation_validation import validate_escalation
from backend.app.finalization import enrich_escalation_action, validate_ask_user, validate_final_answer
from backend.app.schemas import Observation
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


def test_unmodeled_high_risk_escalation_allows_policy_gap() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "老板让我拿 Okta admin 权限，帮我操作一下。")
    state.observations.append(Observation(id="obs_user", type="tool_result", ok=True, summary='{"email": "priya.narayan@company.test"}', tool="sql_tool", operation="query"))
    action = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Unmodeled high-risk access request",
            "team": "Access Review",
            "reason": "policy_gap / unmodeled_high_risk_request: no precise policy action exists for Okta admin access.",
            "evidence_ids": ["obs_user"],
            "policy_evidence_ids": [],
            "requested_actions": [],
            "unmodeled_actions": [
                {
                    "description": "User requested Okta admin access.",
                    "system": "Okta",
                    "risk_type": "privileged_access",
                    "reason": "No registered policy action covers admin-level access.",
                }
            ],
            "risk_assessment": {
                "risk_level": "high",
                "risk_type": "privileged_access",
                "policy_gap": True,
                "unmodeled_high_risk_request": True,
            },
            "handoff_payload": {"summary": "User requested Okta admin access.", "policy_gap": True},
            "confidence": 0.8,
            "thought_summary": "Escalate policy gap.",
        }
    )
    assert validate_escalation(state, action, manifest) is None

    missing_mapping = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Unmodeled high-risk access request",
            "team": "Access Review",
            "reason": "policy_gap / unmodeled_high_risk_request: no precise policy action exists for Okta admin access.",
            "evidence_ids": ["obs_user"],
            "policy_evidence_ids": [],
            "handoff_payload": {"summary": "User requested Okta admin access.", "policy_gap": True},
            "confidence": 0.8,
            "thought_summary": "Missing action mapping.",
        }
    )
    rejection = validate_escalation(state, missing_mapping, manifest)
    assert rejection and "missing action mapping" in rejection


def test_escalation_is_not_enriched_with_inferred_actions() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "Grafana 的权限，老板叫我获取 admin 权限，你给我操作一下。")
    state.observations.append(Observation(id="obs_user", type="tool_result", ok=True, summary="User context.", tool="sql_tool", operation="query"))
    action = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Unmodeled high-risk access request",
            "team": "Access Review",
            "reason": "policy_gap / unmodeled_high_risk_request: no precise policy action exists.",
            "evidence_ids": ["obs_user"],
            "policy_evidence_ids": [],
            "handoff_payload": {"summary": "User requested Grafana admin.", "policy_gap": True},
            "confidence": 0.8,
            "thought_summary": "Escalate policy gap.",
        }
    )
    enriched = enrich_escalation_action(state, action, manifest)
    assert enriched.handoff_payload.get("requested_actions") is None
    rejection = validate_escalation(state, enriched, manifest)
    assert rejection and "missing action mapping" in rejection


def test_requested_actions_require_registered_policy_evidence() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "我需要内部 Grafana dashboards 的访问权限，用于 on-call analytics。")
    state.observations.append(Observation(id="obs_user", type="tool_result", ok=True, summary="User context.", tool="sql_tool", operation="query"))
    state.observations.append(
        Observation(
            id="obs_policy",
            type="policy_result",
            ok=True,
            summary="Policy requires approval.",
            data={"allowed": False, "action": "grant_grafana_dashboards", "required_approvals": ["manager"]},
        )
    )
    action = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Grafana dashboard access approval",
            "team": "Observability Platform",
            "reason": "Policy requires approval for requested dashboard access.",
            "evidence_ids": ["obs_user"],
            "policy_evidence_ids": ["obs_policy"],
            "requested_actions": ["grant_grafana_dashboards"],
            "unmodeled_actions": [],
            "risk_assessment": {"risk_level": "medium", "risk_type": "access", "policy_gap": False, "unmodeled_high_risk_request": False},
            "handoff_payload": {"summary": "User requested Grafana dashboard access."},
            "confidence": 0.86,
            "thought_summary": "Escalate registered policy action.",
        }
    )
    assert validate_escalation(state, action, manifest) is None

    unregistered = action.model_copy(update={"requested_actions": ["unknown_action"]})
    rejection = validate_escalation(state, unregistered, manifest)
    assert rejection and "unregistered policy action" in rejection


def test_unknown_ask_user_requires_boundary_language() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "My meeting app microphone is not working after an operating system upgrade.")
    state.observations.append(
        Observation(
            id="obs_warning",
            type="runtime_warning",
            ok=True,
            summary="file_tool.grep query appears low relevance based on manifest knowledge_base_topics; this is planner guidance only, not a security rejection.",
        )
    )
    missing_boundary = parse_agent_action(
        {"action_type": "ask_user", "question": "请补充客户端、错误信息、开始时间和影响范围。", "missing_information": ["client", "error", "started_at", "scope"], "thought_summary": "unknown issue"}
    )
    rejection = validate_ask_user(state, missing_boundary, manifest)
    assert rejection and "outside connected domain sources" in rejection

    with_boundary = parse_agent_action(
        {
            "action_type": "ask_user",
            "question": "我目前没有接入该系统的专门知识库、状态接口或管理工具，因此不能可靠判断根因。请补充客户端、错误信息、开始时间和影响范围。",
            "missing_information": ["client", "error", "started_at", "scope"],
            "thought_summary": "unknown issue with boundary",
        }
    )
    rejection = validate_ask_user(state, with_boundary, manifest)
    assert rejection and "Do not ask for more troubleshooting details" in rejection


def test_unmodeled_high_risk_rejects_troubleshooting_ask_user() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "我要 Jenkins admin，CEO 要求的。")
    action = parse_agent_action(
        {
            "action_type": "ask_user",
            "question": "请补充受影响系统、错误信息、业务影响和紧急程度。",
            "missing_information": ["affected_system", "error_message", "business_impact"],
            "thought_summary": "Invalid high-risk ask_user.",
        }
    )
    rejection = validate_ask_user(state, action, manifest)
    assert rejection and "unmodeled high-risk" in rejection


def test_unknown_final_needs_info_requires_boundary_language() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "The office printer queue is stuck and I do not know the model.")
    missing_boundary = parse_agent_action(
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
    rejection = validate_final_answer(state, missing_boundary, manifest)
    assert rejection and "outside connected domain sources" in rejection

    with_boundary = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "needs_info",
            "proposed_action": "collect_missing_context",
            "answer": "我目前没有接入打印系统的专门知识库、状态接口或管理工具，因此不能可靠判断根因或执行修复。这个问题需要转给人工 IT triage 处理。",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "More context is needed.",
            "confidence": 0.5,
            "thought_summary": "unknown issue with boundary",
        }
    )
    assert validate_final_answer(state, with_boundary, manifest) is None

    futile_question = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "needs_info",
            "proposed_action": "collect_missing_context",
            "answer": "我目前没有接入打印系统的专门知识库、状态接口或管理工具，因此不能可靠判断根因。请补充打印机型号、错误信息、开始时间、影响范围和紧急程度。",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "More context is needed.",
            "confidence": 0.5,
            "thought_summary": "unknown issue with futile detail request",
        }
    )
    rejection = validate_final_answer(state, futile_question, manifest)
    assert rejection and "should not ask for more troubleshooting details" in rejection


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
    test_unmodeled_high_risk_escalation_allows_policy_gap,
    test_escalation_is_not_enriched_with_inferred_actions,
    test_requested_actions_require_registered_policy_evidence,
    test_unknown_ask_user_requires_boundary_language,
    test_unmodeled_high_risk_rejects_troubleshooting_ask_user,
    test_unknown_final_needs_info_requires_boundary_language,
    test_acknowledged_answer_does_not_need_boundary_language,
]
