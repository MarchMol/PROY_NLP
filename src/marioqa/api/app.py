"""API: `/ask`, `/health`, sesión (`/login`, `/logout`, `/session`), `/audit` (admin) y la UI.

    python -m marioqa.api.app          # o: python tasks.py run

El rol de cada consulta sale de la cookie de sesión firmada; el cuerpo de `/ask` no tiene campo
de rol (un `role` en el JSON se rechaza por `extra="forbid"`).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from marioqa.agent.audit import AuditAccessDenied, AuditLog
from marioqa.agent.factory import audit_path
from marioqa.agent.graph import Agent
from marioqa.config import Settings, get_settings
from marioqa.index.access import Role
from marioqa.logging_setup import get_logger, setup_logging
from marioqa.schemas import AnswerOutput

from .session import COOKIE, SessionSigner, check_admin_token

log = get_logger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_BODY_CHARS = 2000  # tope duro del transporte; el guardrail aplica el límite real (300)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(max_length=MAX_BODY_CHARS)
    version: str | None = Field(default=None, max_length=40)


class AskResponse(BaseModel):
    request_id: str
    role: Role
    output: AnswerOutput
    trace: dict[str, Any]
    latency_s: float
    cost_usd: float
    tokens: dict[str, int]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)


def create_app(
    settings: Settings | None = None,
    agent_factory: Callable[[Settings], Agent] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    signer = SessionSigner(
        settings.session_secret.get_secret_value() if settings.session_secret else None,
        settings.session_ttl_s,
    )
    state: dict[str, Any] = {"agent": None, "error": None}
    lock = threading.Lock()  # pocos usuarios simultáneos: una consulta a la vez

    def load_agent() -> None:
        try:
            if agent_factory is not None:
                state["agent"] = agent_factory(settings)
            else:
                from marioqa.agent.factory import build_agent

                state["agent"] = build_agent(settings)
        except Exception as exc:  # índices ausentes, modelo sin descargar, etc.
            log.exception("No se pudo construir el agente")
            state["error"] = f"{type(exc).__name__}: {exc}"

    @asynccontextmanager
    async def lifespan(_: FastAPI):  # noqa: ANN202
        load_agent()
        yield

    app = FastAPI(title="Mario Odyssey RAG", version="0.1.0", lifespan=lifespan)

    def role_of(cookie: str | None) -> Role:
        return signer.role(cookie)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/health")
    def health() -> dict[str, Any]:
        agent: Agent | None = state["agent"]
        comps: dict[str, Any] = {
            "agent": agent is not None,
            "sqlite": settings.sqlite_path.exists(),
            "llm_provider": str(settings.llm_provider),
            "llm_model": settings.llm_model,
        }
        if agent is not None and agent.search_tool is not None:
            try:
                comps["chunks_indexed"] = agent.search_tool.retriever.store.count()
            except Exception as exc:  # noqa: BLE001 - salud: reportar, no fallar
                comps["chunks_indexed"] = f"error: {exc}"
        ok = agent is not None and comps["sqlite"]
        return {"status": "ok" if ok else "degraded", "components": comps, "error": state["error"]}

    @app.post("/ask", response_model=AskResponse)
    def ask(req: AskRequest, marioqa_session: str | None = Cookie(default=None)) -> AskResponse:
        agent: Agent | None = state["agent"]
        if agent is None:
            raise HTTPException(503, "El sistema no está listo: corre `python tasks.py ingest`.")
        role = role_of(marioqa_session)
        with lock:
            r = agent.ask(req.question, role, req.version if role == "admin" else None)
        return AskResponse(
            request_id=r.request_id,
            role=role,
            output=r.output,
            trace=r.trace,
            latency_s=round(r.latency_s, 3),
            cost_usd=round(r.cost_usd, 6),
            tokens={"input": r.usage.input_tokens, "output": r.usage.output_tokens},
        )

    @app.post("/login")
    def login(req: LoginRequest, response: Response) -> dict[str, str]:
        expected = settings.admin_token.get_secret_value() if settings.admin_token else None
        if not check_admin_token(req.token, expected):
            raise HTTPException(401, "Token inválido")
        response.set_cookie(
            COOKIE,
            signer.issue("admin"),
            httponly=True,
            samesite="strict",
            max_age=settings.session_ttl_s,
        )
        return {"role": "admin"}

    @app.post("/logout")
    def logout(response: Response) -> dict[str, str]:
        response.delete_cookie(COOKIE)
        return {"role": "jugador"}

    @app.get("/session")
    def session(marioqa_session: str | None = Cookie(default=None)) -> dict[str, str]:
        return {"role": role_of(marioqa_session)}

    @app.get("/audit")
    def audit(
        limit: int = 20, marioqa_session: str | None = Cookie(default=None)
    ) -> list[dict[str, Any]]:
        agent: Agent | None = state["agent"]
        log_ = agent.audit if agent is not None and agent.audit else AuditLog(audit_path(settings))
        try:
            return log_.read(role_of(marioqa_session), max(1, min(limit, 200)))
        except AuditAccessDenied as exc:
            raise HTTPException(403, str(exc)) from exc

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    setup_logging(settings.log_level)
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
