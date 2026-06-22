from __future__ import annotations

from backend.app.action_schemas import parse_agent_action
from backend.app.config import get_manifest
from backend.app.runtime_executor import execute_tool_action, tool_relevance_warnings, validate_user_scoped_sql
from backend.app.state import SessionState
from backend.app.tools import GenericRuntime


def test_action_schema() -> None:
    action = parse_agent_action(
        {
            "action_type": "tool_call",
            "tool": "sql_tool",
            "operation": "query",
            "arguments": {"sql": "SELECT 1", "params": {}},
            "thought_summary": "validate schema",
        }
    )
    assert action.action_type == "tool_call"
    try:
        parse_agent_action({"action_type": "final_answer", "answer": "missing fields"})
    except Exception as exc:
        assert "Field required" in str(exc)
    else:
        raise AssertionError("Invalid final_answer action should fail schema validation")


def test_sql_guardrail() -> None:
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    validate_user_scoped_sql(state, "SELECT * FROM employees WHERE email = :email", {"email": "alex.chen@company.test"})
    try:
        validate_user_scoped_sql(state, "SELECT * FROM employees LIMIT 1", {})
    except PermissionError as exc:
        assert "current user_email" in str(exc)
    else:
        raise AssertionError("Broad user directory SQL should be rejected")


def test_file_allowlist() -> None:
    runtime = GenericRuntime(get_manifest())
    try:
        runtime.file.read("backend/app/main.py")
    except PermissionError as exc:
        assert "allowlisted" in str(exc)
    else:
        raise AssertionError("file_tool must reject paths outside allowed roots")


def test_http_allowlist() -> None:
    runtime = GenericRuntime(get_manifest())
    try:
        runtime.http.request("GET", "https://example.com/status")
    except PermissionError as exc:
        assert "Host is not allowlisted" in str(exc)
    else:
        raise AssertionError("http_tool must reject non-allowlisted hosts")
    try:
        runtime.http.request("POST", "http://127.0.0.1:8000/api/status/services")
    except PermissionError as exc:
        assert "Method is not allowlisted" in str(exc)
    else:
        raise AssertionError("http_tool must reject non-allowlisted methods")


def test_relevance_warning_is_soft() -> None:
    manifest = get_manifest()
    runtime = GenericRuntime(manifest)
    state = SessionState(session_id="test", user_email="alex.chen@company.test")
    state.remember("user", "Zoom audio is not working after an operating system upgrade.")
    action = parse_agent_action(
        {
            "action_type": "tool_call",
            "tool": "file_tool",
            "operation": "grep",
            "arguments": {"query": "microphone audio camera", "path": "data/knowledge_base", "top_k": 2},
            "thought_summary": "probe docs",
        }
    )
    warnings = tool_relevance_warnings(state, manifest, action)
    assert warnings, "Low-relevance KB search should produce a runtime warning"
    result = execute_tool_action(state, runtime, manifest, action)
    assert result.call.tool == "file_tool"


TESTS = [
    test_action_schema,
    test_sql_guardrail,
    test_file_allowlist,
    test_http_allowlist,
    test_relevance_warning_is_soft,
]
