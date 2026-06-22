from __future__ import annotations

from typing import Any

from .state import SessionState


def compose_escalation_reply(team: str, payload: dict[str, Any], state: SessionState) -> str:
    employee = state.working_state.get("employee", {})
    requested = payload.get("requested_actions") or []
    requested_text = ", ".join(requested) if requested else payload.get("summary") or "用户报告的问题"
    approvals = sorted(
        {
            approval
            for item in state.observations
            if item.type == "policy_result"
            for approval in item.data.get("required_approvals", [])
        }
    )
    approval_text = f"需要审批：{', '.join(approvals)}。" if approvals else "当前 agent 未接入足够的专门数据源或处理工具，需要人工 triage。"
    person = employee.get("name") or state.user_email or "该员工"
    business_context = state.messages[-1]["content"] if state.messages else ""
    return (
        f"这个请求不能由 agent 直接完成，我已升级给 {team}。\n\n"
        f"请求项：{requested_text.rstrip('。.!?')}。\n"
        f"员工：{person}（{state.user_email or 'unknown'}）。\n"
        f"原因：{approval_text}\n"
        f"业务背景：{business_context}\n\n"
        f"我已经把员工信息、{'policy 结果、' if approvals else ''}已查询的工具证据和对话上下文放入 handoff payload，下一位处理人可以直接接手。"
    )
