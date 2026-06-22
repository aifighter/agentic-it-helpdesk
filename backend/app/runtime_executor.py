from __future__ import annotations

import json
import re
from typing import Any

from .action_schemas import ToolAction
from .manifest_matching import kb_topic_matches, policy_action_keyword_matches
from .state import SessionState
from .tools import GenericRuntime, ToolResult


def execute_tool_action(state: SessionState, runtime: GenericRuntime, manifest: dict[str, Any], action: ToolAction) -> ToolResult:
    tool = action.tool
    operation = action.operation
    args = action.arguments

    if tool == "file_tool" and operation == "list":
        return runtime.file.list(args["path"])
    if tool == "file_tool" and operation == "read":
        return runtime.file.read(args["path"])
    if tool == "file_tool" and operation == "grep":
        return runtime.file.grep(args["query"], args.get("path", "data/knowledge_base"), int(args.get("top_k", 4)))
    if tool == "http_tool" and operation == "request":
        return runtime.http.request(args["method"], args["url"], args.get("params"))
    if tool == "sql_tool" and operation == "query":
        validate_user_scoped_sql(state, args["sql"], args.get("params", {}))
        return runtime.sql.query(args["sql"], args.get("params", {}))
    if tool == "search_tool" and operation == "query":
        return runtime.search.query(args["index"], args["query"], int(args.get("top_k", 3)))
    if tool == "policy_tool" and operation == "evaluate":
        return runtime.policy.evaluate(args["action"], normalized_policy_context(state, args.get("context", {})))
    if tool == "handoff_tool" and operation == "create":
        return runtime.handoff.create(args["payload"])
    raise PermissionError(f"Unknown tool operation: {tool}.{operation}")


def tool_relevance_warnings(state: SessionState, manifest: dict[str, Any], action: ToolAction) -> list[str]:
    warnings: list[str] = []
    if action.tool == "file_tool" and action.operation == "grep":
        args = action.arguments
        if args.get("path", "data/knowledge_base") == "data/knowledge_base" and not kb_query_relevant(state, manifest, args.get("query", "")):
            warnings.append("file_tool.grep query appears low relevance based on manifest knowledge_base_topics; this is planner guidance only, not a security rejection.")
        if kb_search_exhausted(state):
            warnings.append("Previous knowledge base searches returned no matches; consider another evidence source, ask_user, or final/escalate with existing evidence.")
    if action.tool == "policy_tool" and action.operation == "evaluate":
        if not policy_action_relevant(state, manifest, action.arguments.get("action", "")):
            warnings.append("policy_tool.evaluate action appears low relevance based on manifest policy hints; this is planner guidance only, not a security rejection.")
    return warnings


def kb_search_exhausted(state: SessionState) -> bool:
    empty_greps = [
        obs
        for obs in state.observations
        if obs.tool == "file_tool" and obs.operation == "grep" and obs.ok and not obs.data.get("output")
    ]
    return len(empty_greps) >= 2


def kb_query_relevant(state: SessionState, manifest: dict[str, Any], query: str) -> bool:
    text = f"{query} {' '.join(message['content'] for message in state.messages[-4:] if message['role'] == 'user')}"
    return kb_topic_matches(text, manifest.get("knowledge_base_topics", []))


def policy_action_relevant(state: SessionState, manifest: dict[str, Any], action: str) -> bool:
    hints = manifest.get("planner_hints", {})
    user_text = " ".join(message["content"] for message in state.messages[-8:] if message["role"] == "user")
    if policy_action_keyword_matches(action, user_text, hints):
        return True
    evidence_text = " ".join(
        obs.summary
        for obs in state.observations
        if obs.ok and obs.visible and obs.tool in {"file_tool", "http_tool"}
    )
    return policy_action_keyword_matches(action, evidence_text, hints)


def normalized_policy_context(state: SessionState, context: dict[str, Any]) -> dict[str, Any]:
    employee = state.working_state.get("employee", {})
    normalized = dict(context or {})
    normalized.setdefault("user_found", bool(employee) or bool(state.user_email))
    if "mfa_enrolled" not in normalized:
        mfa_status = normalized.get("mfa_status", employee.get("mfa_status"))
        if mfa_status is not None:
            normalized["mfa_enrolled"] = mfa_status == "enrolled"
    if "account_locked" not in normalized and "account_locked" in employee:
        normalized["account_locked"] = bool(employee.get("account_locked"))
    if "no_compromise_signal" not in normalized:
        risk_flag = normalized.get("risk_flag", employee.get("risk_flag"))
        if risk_flag is not None:
            normalized["no_compromise_signal"] = risk_flag in {None, "", "none", "None"}
    if "device_compliant" not in normalized:
        security_posture = normalized.get("security_posture", employee.get("security_posture"))
        if security_posture is not None:
            normalized["device_compliant"] = security_posture == "compliant"
    return normalized


def validate_user_scoped_sql(state: SessionState, sql: str, params: dict[str, Any]) -> None:
    referenced = {match.lower() for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I)}
    user_tables = {"employees", "devices", "employee_access"}
    if not (referenced & user_tables):
        return
    if not state.user_email:
        raise PermissionError("User directory SQL requires a selected user_email.")
    serialized_params = json.dumps(params or {}, ensure_ascii=False).lower()
    sql_lower = sql.lower()
    email = state.user_email.lower()
    if email not in serialized_params and email not in sql_lower:
        raise PermissionError("User directory SQL must be scoped to the current user_email; broad sampling queries are not allowed.")
