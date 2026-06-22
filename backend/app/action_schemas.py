from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


class ToolAction(BaseModel):
    action_type: Literal["tool_call"]
    tool: Literal["file_tool", "http_tool", "sql_tool", "search_tool", "policy_tool", "handoff_tool"]
    operation: str
    arguments: dict[str, Any]
    thought_summary: str = "执行通用工具查询。"


class AskUserAction(BaseModel):
    action_type: Literal["ask_user"]
    question: str
    missing_information: list[str] = Field(default_factory=list)
    thought_summary: str = "需要用户补充信息。"


class FinalAnswerAction(BaseModel):
    action_type: Literal["final_answer"]
    outcome: Literal["resolved", "needs_info"]
    proposed_action: str
    answer: str
    evidence_ids: list[str]
    policy_evidence_ids: list[str]
    decision_rationale: str
    confidence: float = 0.82
    thought_summary: str = "证据和 policy 支持最终回答。"


class EscalateAction(BaseModel):
    action_type: Literal["escalate"]
    title: str
    team: str
    reason: str
    evidence_ids: list[str]
    policy_evidence_ids: list[str] = Field(default_factory=list)
    handoff_payload: dict[str, Any]
    confidence: float = 0.9
    thought_summary: str = "证据或 policy 要求升级人工。"


AgentAction = Annotated[
    Union[ToolAction, AskUserAction, FinalAnswerAction, EscalateAction],
    Field(discriminator="action_type"),
]

ACTION_ADAPTER = TypeAdapter(AgentAction)


def parse_agent_action(raw: dict[str, Any] | None) -> AgentAction:
    if raw is None:
        raise ValueError("Planner did not return a JSON object.")
    return ACTION_ADAPTER.validate_python(raw)
