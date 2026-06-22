from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.action_schemas import parse_agent_action
from backend.app.agent import HelpdeskAgent
from backend.app.compliance import deterministic_compliance_check
from backend.app.config import get_manifest
from backend.app.finalization import enrich_escalation_action, validate_ask_user, validate_escalation, validate_final_answer
from backend.app.runtime_executor import execute_tool_action, tool_relevance_warnings, validate_user_scoped_sql
from backend.app.schemas import AgentStep, Observation
from backend.app.state import SessionState
from backend.app.tools import GenericRuntime

ERROR_API = "http://127.0.0.1:8011"


def main() -> None:
    ensure_data()
    test_action_schema()
    test_acknowledged_final_answer_contract()
    test_case_state_lifecycle_contract()
    test_max_steps_unmodeled_high_risk_escalates()
    test_sql_guardrail()
    test_file_allowlist()
    test_http_allowlist()
    test_relevance_warning_is_soft()
    test_unknown_ask_user_requires_boundary_language()
    test_unmodeled_high_risk_rejects_troubleshooting_ask_user()
    test_unknown_final_needs_info_requires_boundary_language()
    test_agent_meta_answer_does_not_need_boundary_language()
    test_unrelated_policy_cannot_support_resolved()
    test_unmodeled_high_risk_escalation_allows_policy_gap()
    test_policy_gap_escalation_is_not_enriched_with_inferred_actions()
    test_compliance_guardrail_from_manifest()
    test_api_error_exposes_traceback()
    print("PASS runtime/unit tests")


def test_action_schema() -> None:
    action = parse_agent_action(
        {
            "action_type": "tool_call",
            "tool": "sql_tool",
            "operation": "query",
            "arguments": {"sql": "SELECT 1", "params": {}},
            "thought_summary": "validate schema",
        }
    )
    assert action.action_type == "tool_call"
    try:
        parse_agent_action({"action_type": "final_answer", "answer": "missing fields"})
    except Exception as exc:
        assert "Field required" in str(exc)
    else:
        raise AssertionError("Invalid final_answer action should fail schema validation")


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

    risky_ack = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "acknowledged",
            "proposed_action": "none",
            "answer": "I granted a configured high-risk access request.",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "Risky acknowledgement.",
            "confidence": 0.9,
            "thought_summary": "Invalid acknowledgement.",
        }
    )
    rejection = validate_final_answer(state, risky_ack, {**manifest, "risk_guardrails": {"high_risk_terms": [r"high-risk access"]}})
    assert rejection and "high-risk" in rejection


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


def test_max_steps_unmodeled_high_risk_escalates() -> None:
    agent = HelpdeskAgent()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "我要 Jenkins admin，CEO 要求的。")
    payload = agent._max_steps_payload(state)
    assert payload["outcome"] == "escalated"
    assert "unmodeled_high_risk_request" in payload["escalation"].get("risk_type", "")
    assert "错误信息" not in payload["reply"]


def test_sql_guardrail() -> None:
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    validate_user_scoped_sql(state, "SELECT * FROM employees WHERE email = :email", {"email": "alex.chen@company.test"})
    try:
        validate_user_scoped_sql(state, "SELECT * FROM employees LIMIT 1", {})
    except PermissionError as exc:
        assert "current user_email" in str(exc)
    else:
        raise AssertionError("Broad user directory SQL should be rejected")


def test_file_allowlist() -> None:
    runtime = GenericRuntime(get_manifest())
    try:
        runtime.file.read("backend/app/main.py")
    except PermissionError as exc:
        assert "allowlisted" in str(exc)
    else:
        raise AssertionError("file_tool must reject paths outside allowed roots")


def test_http_allowlist() -> None:
    runtime = GenericRuntime(get_manifest())
    try:
        runtime.http.request("GET", "https://example.com/status")
    except PermissionError as exc:
        assert "Host is not allowlisted" in str(exc)
    else:
        raise AssertionError("http_tool must reject non-allowlisted hosts")
    try:
        runtime.http.request("POST", "http://127.0.0.1:8000/api/status/services")
    except PermissionError as exc:
        assert "Method is not allowlisted" in str(exc)
    else:
        raise AssertionError("http_tool must reject non-allowlisted methods")


def test_relevance_warning_is_soft() -> None:
    manifest = get_manifest()
    runtime = GenericRuntime(manifest)
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "Zoom audio is not working after an operating system upgrade.")
    action = parse_agent_action(
        {
            "action_type": "tool_call",
            "tool": "file_tool",
            "operation": "grep",
            "arguments": {"query": "microphone audio camera", "path": "data/knowledge_base", "top_k": 2},
            "thought_summary": "probe docs",
        }
    )
    warnings = tool_relevance_warnings(state, manifest, action)
    assert warnings, "Low-relevance KB search should produce a runtime warning"
    result = execute_tool_action(state, runtime, manifest, action)
    assert result.call.tool == "file_tool"


def test_unrelated_policy_cannot_support_resolved() -> None:
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.observations.append(
        Observation(
            id="obs_policy",
            type="policy_result",
            ok=True,
            summary="Allowed unrelated action.",
            data={"allowed": True, "action": "registered_action"},
        )
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
    state.observations.append(
        Observation(
            id="obs_user",
            type="tool_result",
            ok=True,
            summary='{"email": "priya.narayan@company.test"}',
            tool="sql_tool",
            operation="query",
        )
    )
    action = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Unmodeled high-risk access request",
            "team": "Access Review",
            "reason": "policy_gap / unmodeled_high_risk_request: no precise policy action exists for Okta admin access.",
            "evidence_ids": ["obs_user"],
            "policy_evidence_ids": [],
            "handoff_payload": {
                "summary": "User requested Okta admin access.",
                "requested_actions": [],
                "risk_type": "unmodeled_high_risk_request",
                "policy_gap": True,
            },
            "confidence": 0.8,
            "thought_summary": "Escalate policy gap.",
        }
    )
    assert validate_escalation(state, action, manifest) is None

    ordinary_policy_escalation = action.model_copy(
        update={
            "reason": "Policy requires approval for Grafana dashboard access.",
            "handoff_payload": {"summary": "Grafana dashboard access request.", "requested_actions": ["grant_grafana_dashboards"]},
        }
    )
    rejection = validate_escalation(state, ordinary_policy_escalation, manifest)
    assert rejection and "policy-based escalation must cite policy_evidence_ids" in rejection


def test_policy_gap_escalation_is_not_enriched_with_inferred_actions() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.remember("user", "我要 Jenkins admin，CEO 要求的。")
    action = parse_agent_action(
        {
            "action_type": "escalate",
            "title": "Unmodeled high-risk access request",
            "team": "Access Review",
            "reason": "policy_gap / unmodeled_high_risk_request: no precise policy action exists.",
            "evidence_ids": ["obs_user"],
            "policy_evidence_ids": [],
            "handoff_payload": {
                "summary": "User requested Jenkins admin.",
                "requested_actions": [],
                "risk_type": "unmodeled_high_risk_request",
                "policy_gap": True,
            },
            "confidence": 0.8,
            "thought_summary": "Escalate policy gap.",
        }
    )
    enriched = enrich_escalation_action(state, action, manifest)
    assert enriched.handoff_payload["requested_actions"] == []


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
        {
            "action_type": "ask_user",
            "question": "请补充客户端、错误信息、开始时间和影响范围。",
            "missing_information": ["client", "error", "started_at", "scope"],
            "thought_summary": "unknown issue",
        }
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


def test_agent_meta_answer_does_not_need_boundary_language() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "你能解决哪些问题？")
    action = parse_agent_action(
        {
            "action_type": "final_answer",
            "outcome": "needs_info",
            "proposed_action": "describe_agent_capabilities",
            "answer": "我是 IT Helpdesk agent，可以协助 VPN、Okta 登录或账号锁定、Salesforce 慢加载、pipeline 故障，以及访问权限申请的初步排查、分流或升级。",
            "evidence_ids": [],
            "policy_evidence_ids": [],
            "decision_rationale": "User asked about agent capabilities.",
            "confidence": 0.8,
            "thought_summary": "Answer capability question directly.",
        }
    )
    assert validate_final_answer(state, action, manifest) is None

    incident_state = SessionState(session_id="test", user_email="alex.chen@company.test")
    incident_state.remember("user", "Zoom microphone stopped working after an operating system upgrade.")
    rejection = validate_final_answer(incident_state, action, manifest)
    assert rejection and "outside connected domain sources" in rejection


def test_compliance_guardrail_from_manifest() -> None:
    terms = get_manifest()["risk_guardrails"]["high_risk_terms"]
    result = deterministic_compliance_check(
        draft_action_type="final_answer",
        draft={"outcome": "resolved", "answer": "I completed a configured high-risk access request."},
        observations=[],
        high_risk_terms=[r"high-risk access"],
    )
    assert result and result["compliant"] is False
    assert terms, "Manifest must define risk guardrail terms"

    allowed_policy = Observation(
        id="obs_policy",
        type="policy_result",
        ok=True,
        summary="Policy allowed.",
        data={"allowed": True, "action": "generic_action"},
    )
    clean = deterministic_compliance_check(
        draft_action_type="final_answer",
        draft={"outcome": "resolved", "answer": "Use the documented troubleshooting steps."},
        observations=[allowed_policy],
        high_risk_terms=terms,
    )
    assert clean and clean["compliant"] is True and clean["required_next_action"] == "allow"

    meta = deterministic_compliance_check(
        draft_action_type="final_answer",
        draft={
            "outcome": "needs_info",
            "proposed_action": "describe_agent_capabilities",
            "answer": "你好，我是 IT Helpdesk agent。可以协助 VPN、Okta、Salesforce 和 pipeline 问题。",
        },
        user_messages=[{"role": "user", "content": "你能解决哪些问题？"}],
        observations=[],
        high_risk_terms=terms,
    )
    assert meta and meta["compliant"] is True and meta["required_next_action"] == "allow"

    high_risk_with_unrelated_allow = deterministic_compliance_check(
        draft_action_type="final_answer",
        draft={"outcome": "resolved", "answer": "Use the documented steps and request a restricted-change."},
        observations=[allowed_policy],
        high_risk_terms=[r"restricted-change"],
    )
    assert high_risk_with_unrelated_allow and high_risk_with_unrelated_allow["required_next_action"] == "escalate"


def test_api_error_exposes_traceback() -> None:
    process = start_error_server()
    try:
        response = requests.post(
            f"{ERROR_API}/api/chat",
            json={"user_email": "alex.chen@company.test", "message": "Test provider error exposure."},
            timeout=20,
        )
        assert response.status_code == 500
        data = response.json()
        assert "traceback" in data
        assert "LLM JSON client is disabled" in data["traceback"]
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


def ensure_data() -> None:
    if not (ROOT / "data" / "generated" / "employees.db").exists():
        subprocess.run([sys.executable, "scripts/seed_data.py"], cwd=ROOT, check=True)


def start_error_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["HELPDESK_USE_LLM"] = "0"
    env["LLM_API_KEY"] = ""
    env["DEEPSEEK_API_KEY"] = ""
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8011"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            if requests.get(f"{ERROR_API}/api/health", timeout=1).json().get("status") == "ok":
                return process
        except Exception:
            pass
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Error-test API server did not start")


if __name__ == "__main__":
    main()
