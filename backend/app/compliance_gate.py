from __future__ import annotations

from typing import Any

from .compliance import ComplianceChecker, compliance_summary
from .observations import append_step, tool_calls_from_observations
from .schemas import Observation
from .state import SessionState


def check_compliance(
    *,
    state: SessionState,
    checker: ComplianceChecker,
    draft_action_type: str,
    draft: dict[str, Any],
    next_id,
    llm_api_key: str | None = None,
) -> str | None:
    observations = [obs for obs in state.observations if obs.visible][-20:]
    result = checker.check(
        draft_action_type=draft_action_type,
        draft=draft,
        user_messages=state.messages,
        observations=observations,
        tool_calls=tool_calls_from_observations(observations),
        llm_api_key=llm_api_key,
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
