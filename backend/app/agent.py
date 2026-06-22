from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from .compliance import ComplianceChecker, check_compliance
from .config import get_manifest
from .escalation import validate_escalation
from .finalization import (
    build_ask_user,
    build_escalation,
    build_final_answer,
    enrich_escalation_action,
    validate_ask_user,
    validate_final_answer,
)
from .llm import DeepSeekClient
from .observations import (
    append_step,
    evidence_from_observations,
    observations_by_id,
    runtime_rejection,
    tool_calls_from_observations,
    tool_observation,
    user_request_observation,
    update_working_state_from_observation,
    visible_observations,
)
from .planner_contract import planner_payload, planner_system_prompt
from .runtime_executor import execute_tool_action
from .schemas import AskUserAction, ChatResponse, EscalateAction, FinalAnswerAction, ToolAction, parse_agent_action
from .state import SessionState
from .tools import GenericRuntime

MAX_STEPS = 18


class HelpdeskAgent:
    def __init__(self) -> None:
        self.manifest = get_manifest()
        self.runtime = GenericRuntime(self.manifest)
        self.llm = DeepSeekClient()
        self.compliance = ComplianceChecker(self.llm, self.runtime.policy.rules)
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
        state.observations.append(user_request_observation(self._next_observation_id(), message, state.user_email))
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
                    self._record_action_rejection(state, "ask_user", action.thought_summary, rejection, "planner 必须说明未接入系统边界并重新追问。")
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
            self._record_action_rejection(state, "final_answer", action.thought_summary, rejection, "planner 必须补充 evidence/policy 或改为升级/追问。")
            return None
        if action.outcome == "acknowledged":
            return build_final_answer(state, action)
        if rejection := check_compliance(state=state, checker=self.compliance, draft_action_type="final_answer", draft=action.model_dump(), next_id=self._next_observation_id):
            self._record_action_rejection(state, "final_answer", action.thought_summary, rejection, "Mandatory compliance checker 拒绝 final_answer，planner 必须继续查询、追问或改为升级。")
            return None
        return build_final_answer(state, action)

    def _try_escalation(self, state: SessionState, action: EscalateAction) -> dict[str, Any] | None:
        action = enrich_escalation_action(state, action, self.manifest)
        self._remember_escalation_signal(state, action)
        if rejection := validate_escalation(state, action, self.manifest):
            self._record_action_rejection(state, "escalate", action.thought_summary, rejection, "planner 必须补充 handoff 所需证据。")
            return None
        if rejection := check_compliance(state=state, checker=self.compliance, draft_action_type="escalate", draft=action.model_dump(), next_id=self._next_observation_id):
            self._record_action_rejection(state, "escalate", action.thought_summary, rejection, "Mandatory compliance checker 拒绝 escalate 草稿，planner 必须补充证据或重新生成 handoff。")
            return None
        return build_escalation(state, self.runtime, action, self._next_observation_id)

    def _record_runtime_rejection(self, state: SessionState, reason: str, thought: str):
        obs = runtime_rejection(self._next_observation_id(), reason, thought)
        state.observations.append(obs)
        append_step(state, action_type="runtime_rejection", thought_summary=thought, status="rejected", observation_id=obs.id)
        return obs

    def _record_action_rejection(self, state: SessionState, action_type: str, thought: str, reason: str, guidance: str):
        obs = runtime_rejection(self._next_observation_id(), reason, guidance)
        state.observations.append(obs)
        append_step(state, action_type=action_type, thought_summary=thought, status="rejected")
        append_step(state, action_type="runtime_rejection", thought_summary=guidance, status="rejected", observation_id=obs.id)
        return obs

    def _remember_escalation_signal(self, state: SessionState, action: EscalateAction) -> None:
        if not (action.unmodeled_actions or action.risk_assessment.policy_gap or action.risk_assessment.unmodeled_high_risk_request):
            return
        state.working_state["last_escalation_signal"] = {
            "title": action.title,
            "team": action.team,
            "reason": action.reason,
            "requested_actions": list(action.requested_actions),
            "unmodeled_actions": [item.model_dump(mode="json") for item in action.unmodeled_actions],
            "risk_assessment": action.risk_assessment.model_dump(mode="json"),
            "handoff_payload": dict(action.handoff_payload),
        }

    def _max_steps_payload(self, state: SessionState) -> dict[str, Any]:
        signal = state.working_state.get("last_escalation_signal")
        compliance_requires_escalation = any(
            obs.type == "planner_note"
            and isinstance(obs.data.get("compliance"), dict)
            and obs.data["compliance"].get("required_next_action") == "escalate"
            for obs in state.observations[-8:]
        )
        if signal or compliance_requires_escalation:
            obs = self._record_runtime_rejection(
                state,
                "max_steps reached after structured terminal escalation signals; escalating instead of asking for unrelated troubleshooting details.",
                "达到 max_steps，但上下文已有结构化升级信号，转为人工升级。",
            )
            risk_assessment = (signal or {}).get("risk_assessment") or {"risk_level": "unknown", "risk_type": "unknown"}
            unmodeled_actions = (signal or {}).get("unmodeled_actions") or [
                {
                    "description": "Planner indicated escalation is required but did not complete a terminal action before max_steps.",
                    "system": None,
                    "risk_type": risk_assessment.get("risk_type", "unknown"),
                    "reason": "Max steps reached after structured escalation/compliance signals.",
                }
            ]
            handoff_payload = {
                **((signal or {}).get("handoff_payload") or {}),
                "summary": state.messages[-1]["content"] if state.messages else "Escalation required after max_steps.",
                "requested_actions": (signal or {}).get("requested_actions") or [],
                "unmodeled_actions": unmodeled_actions,
                "risk_assessment": risk_assessment,
                "max_steps": True,
            }
            action = EscalateAction(
                action_type="escalate",
                title=(signal or {}).get("title") or "Escalation required after max_steps",
                team=(signal or {}).get("team") or "General IT Triage",
                reason=(signal or {}).get("reason") or "Structured compliance/escalation signal remained unresolved before max_steps.",
                evidence_ids=[obs.id],
                policy_evidence_ids=[],
                requested_actions=(signal or {}).get("requested_actions") or [],
                unmodeled_actions=unmodeled_actions,
                risk_assessment=risk_assessment,
                handoff_payload=handoff_payload,
                confidence=0.78,
                thought_summary="已有结构化升级信号但未在 max_steps 前收敛，升级给人工。",
            )
            payload = self._try_escalation(state, action)
            if payload:
                return payload
            return {
                "reply": "Planner 已达到最大步骤，但仍未生成通过 runtime 和 compliance 的终止动作。请查看右侧诊断面板中的 runtime rejection、compliance result 和 handoff 草稿，人工 review 当前失败原因。",
                "outcome": "needs_info",
                "confidence": 0.2,
                "decision_rationale": "max_steps reached after structured escalation/compliance signals, but fallback escalation did not pass runtime validation.",
                "evidence_ids": [obs.id],
                "policy_evidence_ids": [],
            }
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
            observations = [obs for obs in visible_observations(state.observations[observation_start_index:]) if obs.type != "user_request"]
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
