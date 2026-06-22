from __future__ import annotations

import os
import json
from typing import Any

import requests

class DeepSeekClient:
    def __init__(self) -> None:
        self.model = os.getenv("LLM_MODEL") or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.base_url = (os.getenv("LLM_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")
        self.max_tokens = int(os.getenv("LLM_MAX_TOKENS") or os.getenv("DEEPSEEK_MAX_TOKENS", "4096"))
        self.thinking = os.getenv("LLM_THINKING") or os.getenv("DEEPSEEK_THINKING", "disabled")
        self.connect_timeout_seconds = float(os.getenv("LLM_CONNECT_TIMEOUT_SECONDS") or os.getenv("DEEPSEEK_CONNECT_TIMEOUT_SECONDS", "10"))
        self.read_timeout_seconds = float(os.getenv("LLM_READ_TIMEOUT_SECONDS") or os.getenv("DEEPSEEK_READ_TIMEOUT_SECONDS", "120"))
        self.llm_enabled = os.getenv("HELPDESK_USE_LLM", "1") != "0"
        self.enabled = self.llm_enabled

    def plan_action(self, system_prompt: str, payload: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
        return self.complete_json(system_prompt, payload, api_key=api_key)

    def complete_json(self, system_prompt: str, payload: dict[str, Any], api_key: str | None = None) -> dict[str, Any]:
        request_api_key = self._request_api_key(api_key)
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.thinking == "disabled":
            self._disable_thinking(body)
        elif self.thinking in {"minimal", "low"}:
            self._minimize_thinking(body)
        elif self.thinking == "enabled":
            self._enable_thinking(body)
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {request_api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=(self.connect_timeout_seconds, self.read_timeout_seconds),
        )
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        content = choice["message"].get("content") or ""
        if not content.strip():
            raise ValueError(
                "LLM provider returned empty JSON content. "
                f"finish_reason={choice.get('finish_reason')!r}, usage={body.get('usage')!r}"
            )
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "LLM provider returned invalid JSON content. "
                f"finish_reason={choice.get('finish_reason')!r}, content_prefix={content[:1000]!r}"
            ) from exc

    def _provider_family(self) -> str:
        value = f"{self.base_url} {self.model}".lower()
        if "deepseek" in value:
            return "deepseek"
        if "dashscope" in value or "bailian" in value or "aliyun" in value or "qwen" in value:
            return "qwen"
        return "openai_compatible"

    def _disable_thinking(self, body: dict[str, Any]) -> None:
        provider = self._provider_family()
        if provider == "deepseek":
            body["thinking"] = {"type": "disabled"}
            return
        if provider == "qwen":
            body["enable_thinking"] = False
            return
        body["thinking"] = {"type": "disabled"}
        body["enable_thinking"] = False

    def _minimize_thinking(self, body: dict[str, Any]) -> None:
        provider = self._provider_family()
        if provider == "deepseek":
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "low"
            return
        if provider == "qwen":
            body["enable_thinking"] = True
            body["thinking_budget"] = 1
            return
        body["reasoning_effort"] = "low"

    def _enable_thinking(self, body: dict[str, Any]) -> None:
        provider = self._provider_family()
        if provider == "deepseek":
            body["thinking"] = {"type": "enabled"}
            return
        if provider == "qwen":
            body["enable_thinking"] = True

    def _request_api_key(self, api_key: str | None) -> str:
        if not self.llm_enabled:
            raise RuntimeError("LLM JSON client is disabled by HELPDESK_USE_LLM=0.")
        request_api_key = (api_key or "").strip()
        if not request_api_key:
            raise RuntimeError("LLM API key is missing. Enter a DeepSeek API key in the frontend.")
        return request_api_key
