from __future__ import annotations

from typing import Any

from .tools import GenericRuntime, ToolResult


def create_handoff_for_escalation(runtime: GenericRuntime, payload: dict[str, Any]) -> ToolResult:
    return runtime.handoff.create(payload)
