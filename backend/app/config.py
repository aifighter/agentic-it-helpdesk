from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def get_manifest() -> dict[str, Any]:
    return load_yaml(ROOT / "domain_manifest.yaml")


def env(name: str, default: str | None = None) -> str | None:
    load_env()
    return os.getenv(name, default)
