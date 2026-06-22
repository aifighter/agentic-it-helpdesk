from __future__ import annotations

import json
import re
from typing import Any

from .schemas import ToolAction
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
    raise PermissionError(f"Unknown tool operation: {tool}.{operation}")


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
