from __future__ import annotations

import subprocess

import requests

from backend.app.compliance import deterministic_compliance_check
from backend.app.escalation import has_policy_gap_marker
from backend.app.schemas import Observation
from tests.test_support import ERROR_API, start_error_server


def test_compliance_guardrail_from_structure() -> None:
    result = deterministic_compliance_check(
        draft_action_type="final_answer",
        draft={"outcome": "resolved", "risk_assessment": {"policy_gap": True}},
        observations=[],
    )
    assert result and result["required_next_action"] == "escalate"

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
    )
    assert clean and clean["compliant"] is True and clean["required_next_action"] == "allow"

    unsupported_escalation = deterministic_compliance_check(
        draft_action_type="escalate",
        draft={"requested_actions": ["missing_policy_action"], "handoff_payload": {}},
        observations=[allowed_policy],
    )
    assert unsupported_escalation and unsupported_escalation["required_next_action"] == "reject"


def test_policy_gap_marker_requires_true_value_or_marker_text() -> None:
    assert not has_policy_gap_marker({"risk_assessment": {"policy_gap": False, "unmodeled_high_risk_request": False}})
    assert has_policy_gap_marker({"risk_assessment": {"policy_gap": True}})
    assert has_policy_gap_marker({"reason": "policy_gap / unmodeled_high_risk_request requires manual review"})


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


TESTS = [
    test_compliance_guardrail_from_structure,
    test_policy_gap_marker_requires_true_value_or_marker_text,
    test_api_error_exposes_traceback,
]
