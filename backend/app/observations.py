from __future__ import annotations

import json
import re
from typing import Any

from .schemas import AgentStep, EvidenceItem, Observation, ToolCall
from .state import SessionState
from .tools import ToolResult, summarize


def plain_text(text: str) -> str:
    text = re.sub(r"[*_`#]+", "", str(text))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tool_observation(next_id: str, result: ToolResult, observation_type: str | None = None) -> Observation:
    obs_type = observation_type or ("policy_result" if result.call.tool == "policy_tool" else "tool_result")
    obs = Observation(
        id=next_id,
        type=obs_type,
        ok=result.call.ok,
        summary=result.call.output_summary,
        tool=result.call.tool,
        operation=result.call.action,
        input_summary=summarize(result.call.input),
        evidence=result.evidence,
        data={"output": result.output, "tool_calls": [result.call.model_dump(mode="json")]},
        visible=True,
    )
    if result.call.tool == "policy_tool" and isinstance(result.output, dict):
        obs.data.update(result.output)
        obs.summary = result.output.get("rationale") or obs.summary
    return obs


def runtime_rejection(next_id: str, reason: str, thought: str) -> Observation:
    return Observation(
        id=next_id,
        type="runtime_rejection",
        ok=False,
        summary=reason,
        data={"required_next_step": thought},
        visible=True,
    )


def runtime_warning(next_id: str, summary: str, thought: str) -> Observation:
    return Observation(
        id=next_id,
        type="runtime_warning",
        ok=True,
        summary=summary,
        data={"planner_guidance": thought},
        visible=True,
    )


def append_step(
    state: SessionState,
    *,
    action_type: str,
    thought_summary: str,
    status: str,
    tool: str | None = None,
    operation: str | None = None,
    observation_id: str | None = None,
) -> None:
    state.steps.append(
        AgentStep(
            step=len(state.steps) + 1,
            action_type=action_type,  # type: ignore[arg-type]
            thought_summary=thought_summary,
            status=status,  # type: ignore[arg-type]
            tool=tool,
            operation=operation,
            observation_id=observation_id,
        )
    )


def update_working_state_from_observation(state: SessionState, obs: Observation) -> None:
    if obs.tool == "sql_tool" and obs.data.get("output"):
        row = obs.data["output"][0]
        if "email" in row:
            state.working_state["employee"] = {**state.working_state.get("employee", {}), **row}
        if "employee_email" in row and "asset_tag" in row:
            state.working_state["device"] = row
            state.working_state["employee"] = {**state.working_state.get("employee", {}), **row}
        if "access_group" in row:
            state.working_state["access"] = obs.data["output"]
    if obs.tool == "policy_tool":
        state.working_state.setdefault("policy_results", {})[obs.data.get("action")] = obs.data


def visible_observations(observations: list[Observation]) -> list[Observation]:
    return [obs for obs in observations if obs.visible][-20:]


def dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen = set()
    output = []
    for item in items:
        key = (item.source, item.title, item.summary)
        if key not in seen:
            seen.add(key)
            output.append(item)
    return output[:16]


def tool_calls_from_observations(observations: list[Observation]) -> list[ToolCall]:
    return [ToolCall.model_validate(call) for obs in observations for call in obs.data.get("tool_calls", [])]


def evidence_from_observations(observations: list[Observation]) -> list[EvidenceItem]:
    return dedupe_evidence([item for obs in observations for item in obs.evidence])


def observations_by_id(observations: list[Observation], ids: list[str]) -> list[Observation]:
    wanted = set(ids)
    return [obs for obs in observations if obs.id in wanted]


def summarize_observations_for_text(observations: list[Observation]) -> str:
    return " ".join(obs.summary for obs in observations if obs.ok and obs.visible)


def summarize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
