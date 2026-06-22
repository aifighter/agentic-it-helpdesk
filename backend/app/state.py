from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import AgentStep, Observation


@dataclass
class SessionState:
    session_id: str
    user_email: str | None = None
    messages: list[dict[str, str]] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    working_state: dict[str, Any] = field(default_factory=dict)

    def remember(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def clear_case_context(self) -> None:
        self.observations.clear()
        self.steps.clear()
        self.working_state.clear()
