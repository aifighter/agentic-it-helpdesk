from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ERROR_API = "http://127.0.0.1:8011"


def ensure_data() -> None:
    if not (ROOT / "data" / "generated" / "employees.db").exists():
        subprocess.run([sys.executable, "scripts/seed_data.py"], cwd=ROOT, check=True)


def start_error_server() -> subprocess.Popen:
    env = os.environ.copy()
    env["HELPDESK_USE_LLM"] = "0"
    env["LLM_API_KEY"] = ""
    env["DEEPSEEK_API_KEY"] = ""
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8011"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        try:
            if requests.get(f"{ERROR_API}/api/health", timeout=1).json().get("status") == "ok":
                return process
        except Exception:
            pass
        time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Error-test API server did not start")
