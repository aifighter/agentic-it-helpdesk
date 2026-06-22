from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
import yaml

from .config import ROOT, env
from .schemas import EvidenceItem, ToolCall


def summarize(value: Any, limit: int = 260) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def tokens(text: str) -> set[str]:
    output = set()
    for part in re.sub(r"[^a-zA-Z0-9]+", " ", text.lower()).split():
        if len(part) <= 2:
            continue
        output.add(part)
        if part.endswith("s") and len(part) > 3:
            output.add(part[:-1])
    return output


@dataclass
class ToolResult:
    output: Any
    call: ToolCall
    evidence: list[EvidenceItem]


class FileTool:
    def __init__(self, allowed_roots: list[str]) -> None:
        self.allowed_roots = [(ROOT / root).resolve() for root in allowed_roots]

    def list(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        output = sorted(str(item.relative_to(ROOT)) for item in resolved.iterdir())
        return ToolResult(output, ToolCall(tool="file_tool", action="list", input={"path": path}, output_summary=summarize(output)), [])

    def read(self, path: str) -> ToolResult:
        resolved = self._resolve(path)
        text = resolved.read_text(encoding="utf-8")
        evidence = [EvidenceItem(source="knowledge_base", title=resolved.name, summary=text[:300], metadata={"path": str(resolved.relative_to(ROOT))})]
        return ToolResult(text, ToolCall(tool="file_tool", action="read", input={"path": path}, output_summary=text[:220]), evidence)

    def grep(self, query: str, path: str = "data/knowledge_base", top_k: int = 4) -> ToolResult:
        root = self._resolve(path)
        query_tokens = tokens(query)
        matches = []
        for file in sorted(root.rglob("*.md")):
            text = file.read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip() if text.splitlines() else file.name
            title_path_tokens = tokens(file.stem + " " + str(file.relative_to(root)) + " " + title)
            body_tokens = tokens(text)
            score = 3 * len(query_tokens & title_path_tokens) + len(query_tokens & body_tokens)
            if score:
                matches.append({"path": str(file.relative_to(ROOT)), "title": title, "score": score, "excerpt": excerpt(text, query_tokens)})
        matches = sorted(matches, key=lambda item: item["score"], reverse=True)[:top_k]
        best_score = matches[0]["score"] if matches else 0
        evidence = [
            EvidenceItem(source="knowledge_base", title=item["title"], summary=item["excerpt"], metadata={"path": item["path"], "score": item["score"]})
            for item in matches
            if item["score"] == best_score
        ]
        return ToolResult(matches, ToolCall(tool="file_tool", action="grep", input={"query": query, "path": path, "top_k": top_k}, output_summary=summarize(matches)), evidence)

    def _resolve(self, path: str) -> Path:
        resolved = (ROOT / path).resolve()
        if not any(resolved == allowed or allowed in resolved.parents for allowed in self.allowed_roots):
            raise PermissionError(f"Path is not allowlisted: {path}")
        return resolved


class HttpTool:
    def __init__(self, allowed_hosts: list[str], allowed_methods: list[str]) -> None:
        self.allowed_hosts = {host.rstrip("/") for host in allowed_hosts}
        self.allowed_methods = {method.upper() for method in allowed_methods}

    def request(self, method: str, url: str, params: dict[str, Any] | None = None) -> ToolResult:
        method = method.upper()
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base not in self.allowed_hosts:
            raise PermissionError(f"Host is not allowlisted: {base}")
        if method not in self.allowed_methods:
            raise PermissionError(f"Method is not allowlisted: {method}")
        response = requests.request(method, url, params=params, timeout=5)
        response.raise_for_status()
        output = response.json()
        evidence = []
        for item in output if isinstance(output, list) else [output]:
            if isinstance(item, dict):
                evidence.append(
                    EvidenceItem(
                        source="system_status",
                        title=item.get("service") or item.get("id") or "HTTP API result",
                        summary=format_http_evidence_summary(item),
                        metadata={key: value for key, value in item.items() if key not in {"summary"}},
                    )
                )
        return ToolResult(output, ToolCall(tool="http_tool", action="request", input={"method": method, "url": url, "params": params or {}}, output_summary=summarize(output)), evidence)


class SqlTool:
    def __init__(self, database: str, allowed_tables: list[str]) -> None:
        self.database = ROOT / database
        self.allowed_tables = set(allowed_tables)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> ToolResult:
        if not sql.strip().lower().startswith("select"):
            raise PermissionError("Only SELECT queries are allowed")
        referenced = {match.lower() for match in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", sql, flags=re.I)}
        if not referenced <= self.allowed_tables:
            raise PermissionError(f"Query references non-allowlisted tables: {sorted(referenced - self.allowed_tables)}")
        with sqlite3.connect(self.database) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(row) for row in conn.execute(sql, params or {})]
        evidence = [
            EvidenceItem(source="user_directory", title=row.get("email") or "SQL row", summary=summarize(row, 220), metadata={"tables": sorted(referenced)})
            for row in rows[:5]
        ]
        return ToolResult(rows, ToolCall(tool="sql_tool", action="query", input={"sql": sql, "params": params or {}}, output_summary=summarize(rows)), evidence)


class SearchTool:
    def __init__(self, indexes: dict[str, dict[str, Any]]) -> None:
        self.indexes = indexes

    def query(self, index: str, query: str, top_k: int = 3) -> ToolResult:
        if index not in self.indexes:
            raise PermissionError(f"Index is not registered: {index}")
        path = ROOT / self.indexes[index]["path"]
        records = json.loads(path.read_text(encoding="utf-8"))
        query_tokens = tokens(query)
        scored = []
        for record in records:
            haystack = " ".join([record.get("title", ""), " ".join(record.get("systems", [])), " ".join(record.get("symptoms", [])), record.get("resolution", "")])
            score = len(query_tokens & tokens(haystack))
            if score:
                scored.append({**record, "score": score})
        output = sorted(scored, key=lambda item: item["score"], reverse=True)[:top_k]
        evidence = [
            EvidenceItem(source="resolution_history", title=item["incident_id"], summary=f"{item['title']}: {item['resolution']}", metadata={"score": item["score"], "systems": item["systems"], "outcome": item["outcome"]})
            for item in output
        ]
        return ToolResult(output, ToolCall(tool="search_tool", action="query", input={"index": index, "query": query, "top_k": top_k}, output_summary=summarize(output)), evidence)


class PolicyTool:
    def __init__(self, rule_file: str) -> None:
        self.rule_file = ROOT / rule_file
        self.rules = yaml.safe_load(self.rule_file.read_text(encoding="utf-8"))

    def evaluate(self, action: str, context: dict[str, Any]) -> ToolResult:
        rule = self.rules.get("actions", {}).get(action, self.rules["default"])
        missing = [condition for condition in rule.get("conditions", []) if not context.get(condition)]
        allowed = bool(rule.get("allowed")) and not missing
        output = {
            "action": action,
            "allowed": allowed,
            "missing_conditions": missing,
            "escalation_team": rule.get("escalation_team"),
            "required_approvals": rule.get("required_approvals", []),
            "required_fields": rule.get("required_fields", []),
            "rationale": rule.get("rationale"),
        }
        evidence = [EvidenceItem(source="policy_rules", title=action, summary=output["rationale"] or "Policy evaluated.", metadata=output)]
        return ToolResult(output, ToolCall(tool="policy_tool", action="evaluate", input={"action": action, "context_keys": sorted(context.keys())}, output_summary=summarize(output)), evidence)


class HandoffTool:
    def create(self, payload: dict[str, Any]) -> ToolResult:
        evidence = [EvidenceItem(source="handoff", title=payload.get("title", "Escalation handoff"), summary=payload.get("reason", ""), metadata={"team": payload.get("team")})]
        return ToolResult(payload, ToolCall(tool="handoff_executor", action="create_handoff", input={"title": payload.get("title"), "team": payload.get("team")}, output_summary=summarize(payload)), evidence)


class GenericRuntime:
    def __init__(self, manifest: dict[str, Any]) -> None:
        runtime = manifest["runtime_tools"]
        self.file = FileTool(runtime["file_tool"]["allowed_roots"])
        self.http = HttpTool(runtime["http_tool"]["allowed_hosts"], runtime["http_tool"]["allowed_methods"])
        self.sql = SqlTool(runtime["sql_tool"]["database"], runtime["sql_tool"]["allowed_tables"])
        self.search = SearchTool(runtime["search_tool"]["indexes"])
        self.policy = PolicyTool(runtime["policy_tool"]["rule_file"])
        self.handoff = HandoffTool()
        self.status_base_url = env("STATUS_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def excerpt(text: str, query_tokens: set[str]) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    for line in lines:
        if tokens(line) & query_tokens:
            return line[:260]
    return " ".join(lines[:2])[:260]


def format_http_evidence_summary(item: dict[str, Any]) -> str:
    summary = item.get("summary") or item.get("risk") or summarize(item, 180)
    if item.get("id"):
        return f"{item['id']}: {summary}"
    if item.get("service"):
        return f"{item['service']}: {summary}"
    return summary
