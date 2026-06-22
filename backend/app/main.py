from __future__ import annotations

import traceback
import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .agent import HelpdeskAgent
from .config import env
from .schemas import ChatRequest, ChatResponse
from .status_api import filter_changes, filter_services


app = FastAPI(title="Agentic IT Helpdesk", version="0.1.0")
cors_origins = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    *[origin.strip() for origin in (env("CORS_ALLOW_ORIGINS") or "").split(",") if origin.strip()],
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = HelpdeskAgent()


@app.exception_handler(Exception)
async def debug_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    origin = request.headers.get("origin")
    headers = {}
    if origin in {"http://localhost:5173", "http://127.0.0.1:5173"}:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        headers=headers,
        content={
            "error": exc.__class__.__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        },
    )


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/llm/health")
def llm_health() -> dict:
    return {
        "planner": "live_llm_structured_json",
        "configured": agent.llm.enabled,
        "server_key_configured": bool(agent.llm.default_api_key),
        "accepts_client_api_key": agent.llm.llm_enabled,
        "model": agent.llm.model,
        "base_url": agent.llm.base_url,
        "timeout_seconds": {
            "connect": agent.llm.connect_timeout_seconds,
            "read": agent.llm.read_timeout_seconds,
        },
        "last_error": None,
    }


@app.get("/api/status/services")
def services(
    service: str | None = Query(default=None),
    region: str | None = Query(default=None),
) -> list[dict]:
    return filter_services(service=service, region=region)


@app.get("/api/status/changes")
def changes(system: str | None = Query(default=None)) -> list[dict]:
    return filter_changes(system=system)


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    return agent.chat(
        message=request.message,
        user_email=request.user_email,
        session_id=request.session_id,
        llm_api_key=request.llm_api_key,
    )


def run() -> None:
    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)


if __name__ == "__main__":
    run()
