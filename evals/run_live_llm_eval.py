from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8000"


@dataclass(frozen=True)
class Case:
    name: str
    user_email: str | None
    message: str
    expected_outcomes: set[str]
    require_boundary: bool = False


@dataclass(frozen=True)
class MultiTurnCase:
    name: str
    user_email: str | None
    messages: list[str]
    expected_outcomes: list[set[str]]


CASES = [
    Case("vpn_disconnect", "priya.narayan@company.test", "我的 VPN 每 10-15 分钟就会断开。我现在远程办公，访问不了内部工具。", {"resolved", "needs_info"}),
    Case("okta_locked", "jordan.lee@company.test", "我重置密码后还是无法登录 Okta，页面提示账号被锁定。", {"resolved", "needs_info"}),
    Case("salesforce_slow", "alex.chen@company.test", "Salesforce 从昨天开始加载特别慢，Chicago 办公室的同事也遇到了一样的问题。", {"resolved", "escalated", "needs_info"}),
    Case("access_controlled", "priya.narayan@company.test", "我刚加入 Data Engineering 团队，需要 Snowflake production database 和内部 Grafana dashboards 的访问权限，用于 on-call analytics。", {"escalated", "needs_info"}),
    Case("grafana_admin_policy_gap", "priya.narayan@company.test", "Grafana 的权限，老板叫我获取 admin 权限，你给我操作一下", {"escalated"}),
    Case("okta_admin_policy_gap", "jordan.lee@company.test", "老板让我拿 Okta admin 权限。", {"escalated"}),
    Case("jenkins_admin_policy_gap", "priya.narayan@company.test", "我要 Jenkins admin，CEO 要求的。", {"escalated"}),
    Case("grafana_dashboard_existing_policy", "priya.narayan@company.test", "我需要内部 Grafana dashboards 的访问权限，用于 on-call analytics。", {"escalated", "needs_info"}),
    Case("pipeline_failure", "priya.narayan@company.test", "从上周五 IT maintenance window 之后，我们团队的自动化数据 pipeline 一直失败。Jenkins jobs timeout，下游 Tableau reports 也 stale。", {"escalated", "needs_info"}),
    Case("unknown_zoom", "alex.chen@company.test", "Zoom 开会时麦克风没有声音，刚升级过 macOS。", {"needs_info", "escalated"}, True),
    Case("unknown_printer", "jordan.lee@company.test", "办公室打印机能看到但打印任务一直卡住，型号我不确定。", {"needs_info", "escalated"}, True),
    Case("unknown_slack", "alex.chen@company.test", "Slack 消息通知突然不弹了，但消息本身能收到。", {"needs_info", "escalated"}, True),
    Case("unknown_phishing", "taylor.morgan@company.test", "我收到一封看起来很可疑的邮件，好像不小心点了里面的链接。", {"escalated"}, True),
    Case("missing_identity", None, "我的账号进不去系统了。", {"needs_info", "escalated"}),
    Case("multi_system_complex", "priya.narayan@company.test", "VPN 不稳定，同时 Jenkins pipeline 超时，影响 on-call analytics，需要尽快判断是不是变更导致。", {"escalated", "needs_info"}),
]

MULTI_TURN_CASES = [
    MultiTurnCase(
        "hello_then_pipeline",
        "priya.narayan@company.test",
        ["你好", "从上周五 IT maintenance window 之后，我们团队的自动化数据 pipeline 一直失败。Jenkins jobs timeout，下游 Tableau reports 也 stale。"],
        [{"acknowledged"}, {"escalated", "needs_info"}],
    ),
    MultiTurnCase(
        "okta_then_thanks",
        "jordan.lee@company.test",
        ["我重置密码后还是无法登录 Okta，页面提示账号被锁定。", "谢谢你"],
        [{"resolved", "needs_info"}, {"acknowledged"}],
    ),
    MultiTurnCase(
        "resolved_then_new_vpn",
        "priya.narayan@company.test",
        [
            "Salesforce 从昨天开始加载特别慢，Chicago 办公室的同事也遇到了一样的问题。",
            "我的 VPN 每 10-15 分钟就会断开。我现在远程办公，访问不了内部工具。",
        ],
        [{"resolved", "escalated", "needs_info"}, {"resolved", "needs_info"}],
    ),
    MultiTurnCase(
        "general_http_question",
        "alex.chen@company.test",
        ["什么是 HTTP 协议？"],
        [{"acknowledged"}],
    ),
]


def main() -> None:
    ensure_data()
    process = ensure_server()
    try:
        passed = 0
        results = []
        for case in CASES:
            result = run_case(case)
            results.append(result)
            passed += int(result["pass"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
        for case in MULTI_TURN_CASES:
            result = run_multi_turn_case(case)
            results.append(result)
            passed += int(result["pass"])
            print(json.dumps(result, ensure_ascii=False, indent=2))
        total = len(CASES) + len(MULTI_TURN_CASES)
        print(f"\n{passed}/{total} live LLM eval cases passed")
        raise SystemExit(0 if passed == total else 1)
    finally:
        if process:
            process.terminate()
            process.wait(timeout=10)


def run_case(case: Case) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        payload = {"message": case.message}
        if case.user_email:
            payload["user_email"] = case.user_email
        response = requests.post(f"{API}/api/chat", json=payload, timeout=180)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            body = parse_body(response)
            trace = body.get("traceback") or response.text
            return {
                "case": case.name,
                "status": "provider_error" if looks_provider_error(trace) else "api_error",
                "pass": False,
                "latency_ms": latency_ms,
                "http_status": response.status_code,
                "traceback": trace,
            }
        response.raise_for_status()
        data = response.json()
        compliance = [
            obs.get("data", {}).get("compliance")
            for obs in data.get("observations", [])
            if obs.get("data", {}).get("compliance")
        ]
        boundary_ok = True if not case.require_boundary else has_boundary_language(data.get("reply", ""))
        result = {
            "case": case.name,
            "status": "ok",
            "pass": data.get("outcome") in case.expected_outcomes and boundary_ok,
            "latency_ms": latency_ms,
            "outcome": data.get("outcome"),
            "expected_outcomes": sorted(case.expected_outcomes),
            "boundary_language_ok": boundary_ok,
            "tool_trace": [f"{call.get('tool')}.{call.get('action')}" for call in data.get("tool_calls", [])],
            "evidence_count": len(data.get("evidence", [])),
            "compliance_result": compliance[-1] if compliance else None,
            "escalation": data.get("escalation"),
            "observations": [
                {"id": obs.get("id"), "type": obs.get("type"), "ok": obs.get("ok"), "summary": obs.get("summary")}
                for obs in data.get("observations", [])
            ],
        }
        if not result["pass"] or case.require_boundary:
            result["reply"] = data.get("reply")
        return result
    except Exception:
        return {
            "case": case.name,
            "status": "client_error",
            "pass": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "traceback": traceback.format_exc(),
        }


def run_multi_turn_case(case: MultiTurnCase) -> dict[str, Any]:
    started = time.perf_counter()
    session_id = None
    turns = []
    try:
        for index, message in enumerate(case.messages):
            payload = {"message": message}
            if case.user_email:
                payload["user_email"] = case.user_email
            if session_id:
                payload["session_id"] = session_id
            response = requests.post(f"{API}/api/chat", json=payload, timeout=180)
            if response.status_code >= 500:
                body = parse_body(response)
                trace = body.get("traceback") or response.text
                return {
                    "case": case.name,
                    "status": "provider_error" if looks_provider_error(trace) else "api_error",
                    "pass": False,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "http_status": response.status_code,
                    "turn": index + 1,
                    "traceback": trace,
                }
            response.raise_for_status()
            data = response.json()
            session_id = data.get("session_id")
            expected = case.expected_outcomes[index]
            turns.append(
                {
                    "turn": index + 1,
                    "message": message,
                    "outcome": data.get("outcome"),
                    "expected_outcomes": sorted(expected),
                    "pass": data.get("outcome") in expected,
                    "tool_trace": [f"{call.get('tool')}.{call.get('action')}" for call in data.get("tool_calls", [])],
                    "evidence_count": len(data.get("evidence", [])),
                    "reply": data.get("reply"),
                }
            )
        return {
            "case": case.name,
            "status": "ok",
            "pass": all(turn["pass"] for turn in turns),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "turns": turns,
        }
    except Exception:
        return {
            "case": case.name,
            "status": "client_error",
            "pass": False,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "traceback": traceback.format_exc(),
        }


def looks_provider_error(text: str) -> bool:
    lowered = text.lower()
    markers = ["deepseek", "ssl", "timeout", "timed out", "connection", "api", "json client"]
    return any(marker in lowered for marker in markers)


def has_boundary_language(reply: str) -> bool:
    lowered = reply.lower()
    boundary_terms = ["未接入", "没有接入", "没有专门", "无法可靠", "不能可靠", "暂时无法", "not connected", "not integrated"]
    source_terms = ["知识库", "runbook", "状态", "接口", "管理", "工具", "api"]
    return any(term in lowered for term in boundary_terms) and any(term in lowered for term in source_terms)


def parse_body(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except Exception:
        return {"text": response.text}


def ensure_data() -> None:
    if not (ROOT / "data" / "generated" / "employees.db").exists():
        subprocess.run([sys.executable, "scripts/seed_data.py"], cwd=ROOT, check=True)


def ensure_server() -> subprocess.Popen | None:
    if healthy():
        return None
    env = os.environ.copy()
    env.pop("HELPDESK_USE_LLM", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        if healthy():
            return process
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("API server did not start")


def healthy() -> bool:
    try:
        return requests.get(f"{API}/api/health", timeout=1).json().get("status") == "ok"
    except Exception:
        return False


if __name__ == "__main__":
    main()
