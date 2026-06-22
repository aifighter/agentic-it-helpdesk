from __future__ import annotations

from typing import Any


def conversation_text(messages: list[dict[str, str]]) -> str:
    return " ".join(item["content"] for item in messages if item["role"] == "user")


def kb_topic_matches(text: str, topics: list[dict[str, Any]]) -> bool:
    lower = text.lower()
    return any(any(keyword.lower() in lower for keyword in topic.get("keywords", [])) for topic in topics)


def matched_services(text: str, hints: dict[str, Any]) -> list[str]:
    lower = text.lower()
    services = []
    for item in hints.get("services", []):
        if any(keyword.lower() in lower for keyword in item.get("keywords", [])):
            services.append(item["name"])
    return services[:4]


def needs_change_log(text: str, hints: dict[str, Any]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in hints.get("change_log_keywords", []))


def matched_policy_actions(text: str, hints: dict[str, Any]) -> list[str]:
    lower = text.lower()
    actions = []
    for item in hints.get("policy_actions", []):
        if any(keyword.lower() in lower for keyword in item.get("keywords", [])):
            if item["action"] not in actions:
                actions.append(item["action"])
    return actions[:4]


def policy_action_keyword_matches(action: str, text: str, hints: dict[str, Any]) -> bool:
    lower = text.lower()
    for item in hints.get("policy_actions", []):
        if item.get("action") == action:
            return any(keyword.lower() in lower for keyword in item.get("keywords", []))
    return False


def needs_access_context(actions: list[str], hints: dict[str, Any]) -> bool:
    configured = {
        item["action"]
        for item in hints.get("policy_actions", [])
        if item.get("requires_context")
    }
    return any(action in configured for action in actions)


def has_access_context(text: str, hints: dict[str, Any]) -> bool:
    lower = text.lower()
    return any(keyword.lower() in lower for keyword in hints.get("access_context_keywords", []))
