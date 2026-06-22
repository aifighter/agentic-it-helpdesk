from __future__ import annotations

import os

from backend.app.action_schemas import parse_agent_action
from backend.app.config import get_manifest
from backend.app import llm as llm_module
from backend.app.llm import DeepSeekClient
from backend.app.planner_contract import tool_schemas
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
    try:
        parse_agent_action(
            {
                "action_type": "tool_call",
                "tool": "handoff_tool",
                "operation": "create",
                "arguments": {"payload": {}},
                "thought_summary": "invalid direct handoff",
            }
        )
    except Exception as exc:
        assert "handoff_tool" in str(exc)
    else:
        raise AssertionError("Planner must not be able to call handoff_tool directly")


def test_handoff_not_planner_visible() -> None:
    exposed_tools = {schema["tool"] for schema in tool_schemas(get_manifest())}
    assert exposed_tools == {"file_tool", "http_tool", "sql_tool", "search_tool", "policy_tool"}


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


def test_request_scoped_llm_api_key_is_used_without_server_key() -> None:
    previous_env = {name: os.environ.get(name) for name in ["LLM_API_KEY", "DEEPSEEK_API_KEY", "HELPDESK_USE_LLM"]}
    previous_post = llm_module.requests.post
    captured: dict[str, str] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}]}

    def fake_post(url, headers, json, timeout):
        captured["authorization"] = headers["Authorization"]
        return FakeResponse()

    try:
        os.environ["LLM_API_KEY"] = ""
        os.environ["DEEPSEEK_API_KEY"] = ""
        os.environ["HELPDESK_USE_LLM"] = "1"
        llm_module.requests.post = fake_post
        client = DeepSeekClient()
        assert client.enabled is False
        assert client.complete_json("system", {"hello": "world"}, api_key="sk-from-ui") == {"ok": True}
        assert captured["authorization"] == "Bearer sk-from-ui"
    finally:
        llm_module.requests.post = previous_post
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


TESTS = [
    test_action_schema,
    test_handoff_not_planner_visible,
    test_sql_guardrail,
    test_file_allowlist,
    test_http_allowlist,
    test_relevance_warning_is_soft,
    test_request_scoped_llm_api_key_is_used_without_server_key,
]
