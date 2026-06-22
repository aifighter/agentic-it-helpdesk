from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from .action_schemas import AskUserAction, EscalateAction, FinalAnswerAction, ToolAction, parse_agent_action
from .compliance_gate import check_compliance
from .compliance import ComplianceChecker
from .config import get_manifest
from .finalization import (
    build_ask_user,
    build_escalation,
    build_final_answer,
    enrich_escalation_action,
    validate_ask_user,
    validate_final_answer,
)
from .escalation_validation import validate_escalation
from .llm import DeepSeekClient
from .observations import (
    append_step,
    evidence_from_observations,
    observations_by_id,
    runtime_rejection,
    runtime_warning,
    tool_calls_from_observations,
    tool_observation,
    update_working_state_from_observation,
    visible_observations,
)
from .planner_contract import planner_payload, planner_system_prompt
from .risk_guardrails import has_unmodeled_high_risk_text, state_high_risk_text, unmodeled_high_risk_config, configured_high_risk_terms
from .runtime_executor import execute_tool_action, tool_relevance_warnings
from .schemas import ChatResponse
from .state import SessionState
from .tools import GenericRuntime

MAX_STEPS = 18


class HelpdeskAgent:
    def __init__(self) -> None:
        self.manifest = get_manifest()
        self.runtime = GenericRuntime(self.manifest)
        self.llm = DeepSeekClient()
        self.compliance = ComplianceChecker(
            self.llm,
            self.runtime.policy.rules,
            configured_high_risk_terms(self.manifest),
        )
        self.sessions: dict[str, SessionState] = {}

    def chat(self, message: str, user_email: str | None = None, session_id: str | None = None) -> ChatResponse:
        state = self._get_state(session_id)
        state.remember("user", message)
        if user_email:
            state.user_email = user_email
        if email := extract_email(message):
            state.user_email = email

        start_index = len(state.steps)
        observation_start_index = len(state.observations)
        working_state_before_turn = deepcopy(state.working_state)
        final_payload: dict[str, Any] | None = None
        for _ in range(MAX_STEPS):
            try:
                action = parse_agent_action(self._plan_next_action(state))
            except ValidationError as exc:
                self._record_runtime_rejection(state, f"Planner action schema validation failed: {exc}", "Planner 输出的 action 结构不合法，要求 planner 重新生成。")
                continue

            if isinstance(action, ToolAction):
                self._execute_tool_call(state, action)
                continue
            if isinstance(action, AskUserAction):
                if rejection := validate_ask_user(state, action, self.manifest):
                    self._record_runtime_rejection(state, rejection, "ask_user 被 runtime 拒绝，planner 必须说明未接入系统边界并重新追问。")
                    continue
                final_payload = build_ask_user(state, action)
                break
            if isinstance(action, FinalAnswerAction):
                final_payload = self._try_final_answer(state, action)
                if final_payload:
                    break
                continue
            if isinstance(action, EscalateAction):
                final_payload = self._try_escalation(state, action)
                if final_payload:
                    break
                continue

        if final_payload is None:
            final_payload = self._max_steps_payload(state)

        state.remember("assistant", final_payload["reply"])
        response = self._response(state, start_index, observation_start_index, final_payload)
        if final_payload["outcome"] in {"resolved", "escalated"}:
            state.clear_case_context()
        elif final_payload["outcome"] == "acknowledged":
            del state.steps[start_index:]
            del state.observations[observation_start_index:]
            state.working_state = working_state_before_turn
        return response

    def _plan_next_action(self, state: SessionState) -> dict[str, Any]:
        payload = planner_payload(state, self.manifest)
        return self.llm.plan_action(planner_system_prompt(), payload)

    def _execute_tool_call(self, state: SessionState, action: ToolAction) -> None:
        try:
            self._record_runtime_warnings(state, tool_relevance_warnings(state, self.manifest, action), action.thought_summary)
            result = execute_tool_action(state, self.runtime, self.manifest, action)
            obs = tool_observation(self._next_observation_id(), result)
            state.observations.append(obs)
            update_working_state_from_observation(state, obs)
            append_step(state, action_type="tool_call", thought_summary=action.thought_summary, status="ok", tool=action.tool, operation=action.operation, observation_id=obs.id)
        except Exception as exc:
            obs = self._record_runtime_rejection(state, f"{action.tool}.{action.operation} rejected: {exc}", action.thought_summary)
            append_step(state, action_type="tool_call", thought_summary=action.thought_summary, status="rejected", tool=action.tool, operation=action.operation, observation_id=obs.id)

    def _try_final_answer(self, state: SessionState, action: FinalAnswerAction) -> dict[str, Any] | None:
        if rejection := validate_final_answer(state, action, self.manifest):
            self._record_runtime_rejection(state, rejection, "final_answer 被 runtime 拒绝，planner 必须补充 evidence/policy 或改为升级/追问。")
            return None
        if action.outcome == "acknowledged":
            return build_final_answer(state, action)
        if rejection := check_compliance(state=state, checker=self.compliance, draft_action_type="final_answer", draft=action.model_dump(), next_id=self._next_observation_id):
            self._record_runtime_rejection(state, rejection, "Mandatory compliance checker 拒绝 final_answer，planner 必须继续查询、追问或改为升级。")
            return None
        return build_final_answer(state, action)

    def _try_escalation(self, state: SessionState, action: EscalateAction) -> dict[str, Any] | None:
        action = enrich_escalation_action(state, action, self.manifest)
        if rejection := validate_escalation(state, action, self.manifest):
            self._record_runtime_rejection(state, rejection, "escalate 被 runtime 拒绝，planner 必须补充 handoff 所需证据。")
            return None
        if rejection := check_compliance(state=state, checker=self.compliance, draft_action_type="escalate", draft=action.model_dump(), next_id=self._next_observation_id):
            self._record_runtime_rejection(state, rejection, "Mandatory compliance checker 拒绝 escalate 草稿，planner 必须补充证据或重新生成 handoff。")
            return None
        return build_escalation(state, self.runtime, action, self._next_observation_id)

    def _record_runtime_rejection(self, state: SessionState, reason: str, thought: str):
        obs = runtime_rejection(self._next_observation_id(), reason, thought)
        state.observations.append(obs)
        append_step(state, action_type="runtime_rejection", thought_summary=thought, status="rejected", observation_id=obs.id)
        return obs

    def _record_runtime_warnings(self, state: SessionState, warnings: list[str], thought: str) -> None:
        for warning in warnings:
            obs = runtime_warning(self._next_observation_id(), warning, thought)
            state.observations.append(obs)

    def _max_steps_payload(self, state: SessionState) -> dict[str, Any]:
        if has_unmodeled_high_risk_text(self.manifest, state_high_risk_text(state)):
            config = unmodeled_high_risk_config(self.manifest)
            obs = self._record_runtime_rejection(
                state,
                "max_steps reached for unmodeled_high_risk_request / policy_gap; escalating instead of asking troubleshooting questions.",
                "达到 max_steps，但上下文是未建模高风险请求，转为人工升级。",
            )
            action = EscalateAction(
                action_type="escalate",
                title="Unmodeled high-risk request",
                team=config.get("escalation_team") or "Access Review",
                reason="policy_gap / unmodeled_high_risk_request: configured high-risk access/change semantics were detected, but no precise allowed policy action can support direct resolution.",
                evidence_ids=[obs.id],
                policy_evidence_ids=[],
                handoff_payload={
                    "summary": state.messages[-1]["content"] if state.messages else "Unmodeled high-risk request",
                    "requested_actions": [],
                    "unmodeled_actions": [
                        {
                            "description": state.messages[-1]["content"] if state.messages else "Unmodeled high-risk request",
                            "system": None,
                            "risk_type": "unknown",
                            "reason": "No exact registered policy action can be safely evaluated for this high-risk request.",
                        }
                    ],
                    "risk_type": "unmodeled_high_risk_request",
                    "risk_assessment": {
                        "risk_level": "high",
                        "risk_type": "unknown",
                        "policy_gap": True,
                        "unmodeled_high_risk_request": True,
                    },
                    "policy_gap": True,
                },
                confidence=0.78,
                thought_summary="未建模高风险请求不能继续追问排障字段，升级给人工审批。",
            )
            payload = self._try_escalation(state, action)
            if payload:
                return payload
        return build_ask_user(
            state,
            AskUserAction(
                action_type="ask_user",
                question="我已经完成多步查询，但还不能可靠收敛。请补充受影响系统、错误信息、业务影响和紧急程度。",
                missing_information=["affected_system", "error_or_symptom", "business_impact"],
                thought_summary="达到 max_steps，转为明确追问以避免编造结论。",
            ),
        )

    def _response(self, state: SessionState, start_index: int, observation_start_index: int, final_payload: dict[str, Any]) -> ChatResponse:
        step_slice = state.steps[start_index:]
        if final_payload["outcome"] == "acknowledged":
            observations = visible_observations(state.observations[observation_start_index:])
        else:
            observations = visible_observations(state.observations)
        evidence_ids = list(final_payload.get("evidence_ids", [])) + list(final_payload.get("policy_evidence_ids", []))
        evidence_observations = observations_by_id(state.observations, evidence_ids) if evidence_ids else []
        return ChatResponse(
            session_id=state.session_id,
            reply=final_payload["reply"],
            outcome=final_payload["outcome"],
            confidence=final_payload["confidence"],
            agent_steps=step_slice,
            observations=observations,
            tool_calls=tool_calls_from_observations(observations),
            evidence=evidence_from_observations(evidence_observations),
            diagnostic_summary=[step.thought_summary for step in step_slice if step.thought_summary],
            decision_rationale=final_payload["decision_rationale"],
            escalation=final_payload.get("escalation"),
        )

    def _get_state(self, session_id: str | None) -> SessionState:
        sid = session_id or str(uuid.uuid4())
        if sid not in self.sessions:
            self.sessions[sid] = SessionState(session_id=sid)
        return self.sessions[sid]

    def _next_observation_id(self) -> str:
        return f"obs_{uuid.uuid4().hex[:8]}"


def extract_email(text: str) -> str | None:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    return match.group(0).lower() if match else None
