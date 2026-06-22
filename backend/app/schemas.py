from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


Outcome = Literal["resolved", "needs_info", "escalated", "acknowledged"]


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    user_email: str | None = None


class ToolCall(BaseModel):
    tool: str
    action: str
    input: dict[str, Any]
    output_summary: str
    ok: bool = True


class EvidenceItem(BaseModel):
    source: str
    title: str
    summary: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class Observation(BaseModel):
    id: str
    type: Literal["tool_result", "runtime_rejection", "runtime_warning", "policy_result", "handoff", "planner_note"]
    ok: bool = True
    summary: str
    tool: str | None = None
    operation: str | None = None
    input_summary: str | None = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    visible: bool = True


class ComplianceResult(BaseModel):
    compliant: bool
    risk_level: Literal["low", "medium", "high"]
    reason: str
    required_next_action: Literal["allow", "reject", "escalate", "ask_user"]
    confidence: float


class AgentStep(BaseModel):
    step: int
    action_type: Literal["tool_call", "ask_user", "final_answer", "escalate", "runtime_rejection", "compliance_check"]
    thought_summary: str
    status: Literal["ok", "rejected", "error"]
    tool: str | None = None
    operation: str | None = None
    observation_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    outcome: Outcome
    confidence: float
    agent_steps: list[AgentStep] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    tool_calls: list[ToolCall]
    evidence: list[EvidenceItem]
    diagnostic_summary: list[str]
    decision_rationale: str
    escalation: dict[str, Any] | None = None


class ServiceStatus(BaseModel):
    service: str
    status: str
    regions: list[str]
    summary: str
    last_updated: str


class ChangeRecord(BaseModel):
    id: str
    date: str
    systems: list[str]
    summary: str
    risk: str
