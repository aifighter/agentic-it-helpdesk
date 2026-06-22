from __future__ import annotations

from backend.app.config import get_manifest
from backend.app.escalation import validate_escalation
from backend.app.finalization import enrich_escalation_action
from backend.app.planner_contract import planner_payload, planner_system_prompt
from backend.app.schemas import Observation, parse_agent_action
from backend.app.state import SessionState


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
    assert rejection and "requires action mapping" in rejection


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
    assert rejection and "requires action mapping" in rejection


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


def test_uncited_grep_match_does_not_block_escalation() -> None:
    manifest = get_manifest()
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.observations.append(
        Observation(
            id="obs_grep",
            type="tool_result",
            ok=True,
            summary="KB grep found production_access.md",
            tool="file_tool",
            operation="grep",
            data={"output": [{"path": "data/knowledge_base/access/production_access.md"}]},
        )
    )
    state.observations.append(Observation(id="obs_user", type="user_request", ok=True, summary="Need Grafana dashboard access."))
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
            "thought_summary": "Escalate with policy evidence.",
        }
    )
    assert validate_escalation(state, action, manifest) is None

    cited_grep = action.model_copy(update={"evidence_ids": ["obs_user", "obs_grep"]})
    rejection = validate_escalation(state, cited_grep, manifest)
    assert rejection and "file_tool.grep found a KB match" in rejection


def test_planner_payload_separates_policy_inventory_from_kb_evidence() -> None:
    state = SessionState(session_id="test", user_email="priya.narayan@company.test")
    state.observations.append(
        Observation(
            id="obs_kb",
            type="tool_result",
            ok=True,
            summary="KB read.",
            tool="file_tool",
            operation="read",
            data={"path": "data/knowledge_base/access/production_access.md"},
        )
    )
    state.observations.append(
        Observation(
            id="obs_policy",
            type="policy_result",
            ok=True,
            summary="Policy requires approval.",
            data={"allowed": False, "action": "grant_snowflake_production"},
        )
    )
    inventory = planner_payload(state, get_manifest())["terminal_action_evidence_inventory"]
    assert inventory["policy_result_observations"] == [
        {"observation_id": "obs_policy", "action": "grant_snowflake_production", "allowed": False}
    ]
    assert inventory["kb_read_observations"] == [
        {"observation_id": "obs_kb", "path": "data/knowledge_base/access/production_access.md", "title": None}
    ]
    assert "cannot replace policy_tool.evaluate" in inventory["note"]


def test_planner_prompt_requires_policy_inventory_before_requested_actions() -> None:
    prompt = planner_system_prompt()
    assert "terminal_action_evidence_inventory.policy_result_observations" in prompt
    assert "不要把尚未出现在" in prompt
    assert "先输出 tool_call policy_tool.evaluate" in prompt
    assert "terminal action 还没准备好" in prompt


TESTS = [
    test_unmodeled_high_risk_escalation_allows_policy_gap,
    test_escalation_is_not_enriched_with_inferred_actions,
    test_requested_actions_require_registered_policy_evidence,
    test_uncited_grep_match_does_not_block_escalation,
    test_planner_payload_separates_policy_inventory_from_kb_evidence,
    test_planner_prompt_requires_policy_inventory_before_requested_actions,
]
