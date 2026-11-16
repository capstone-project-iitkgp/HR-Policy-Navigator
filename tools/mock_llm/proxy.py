"""FastAPI proxy that fronts a local OpenAI-compatible backend (LM Studio, Ollama, ...).

It rewrites the model name to a fixed alias, emulates the shared API's limits, and
passes everything else (tool calls, reasoning text, SSE chunks) through untouched.
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .limits import Limiter, Rejection, apply_token_budget
from .models import ModelSpec

log = logging.getLogger("mock_llm")

# Local generation can be slow; only the connect timeout is short.
BACKEND_TIMEOUT = httpx.Timeout(connect=5.0, read=600.0, write=30.0, pool=5.0)


@dataclass
class Settings:
    backend_url: str = "http://localhost:1234/v1"
    limits: bool = True
    queue: bool = False
    require_key: bool = False
    log_bodies: bool = False


def error_response(
    status: int, code: str, message: str, retry_after: int | None = None
) -> JSONResponse:
    headers = {"Retry-After": str(retry_after)} if retry_after else None
    return JSONResponse(
        {"error": {"message": message, "type": code, "code": code}},
        status_code=status,
        headers=headers,
    )


def _reject(r: Rejection) -> JSONResponse:
    return error_response(r.status, r.code, r.message, r.retry_after)


def _rewrite_model(obj: Any, alias: str) -> Any:
    if isinstance(obj, dict) and "model" in obj:
        obj["model"] = alias
    return obj


def create_app(
    settings: Settings,
    spec: ModelSpec,
    backend_model_id: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    limiter = Limiter(enabled=settings.limits, queue=settings.queue)
    base = settings.backend_url.rstrip("/") + "/"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.client = httpx.AsyncClient(
            base_url=base, timeout=BACKEND_TIMEOUT, transport=transport
        )
        yield
        await app.state.client.aclose()

    app = FastAPI(title="mock_llm", lifespan=lifespan)
    app.state.limiter = limiter  # exposed for tests

    def check_auth(request: Request) -> JSONResponse | None:
        if not settings.require_key:
            return None
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer ") or not header[7:].strip():
            return error_response(401, "invalid_api_key", "Missing bearer token.")
        return None

    def log_line(status: int, started: float, usage: dict | None, notes: list[str]) -> None:
        usage = usage or {}
        log.info(
            "POST /v1/chat/completions status=%s model=%s prompt_tokens=%s "
            "completion_tokens=%s latency=%.2fs%s",
            status, spec.alias, usage.get("prompt_tokens", "-"),
            usage.get("completion_tokens", "-"), time.monotonic() - started,
            f" notes=[{'; '.join(notes)}]" if notes else "",
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        reachable = True
        try:
            r = await app.state.client.get("models")
            reachable = r.status_code < 500
        except httpx.HTTPError:
            reachable = False
        return {
            "status": "ok" if reachable else "backend_unreachable",
            "model": spec.alias,
            "backend_model": backend_model_id,
            "backend_url": settings.backend_url,
            "limits": settings.limits,
            "queue": settings.queue,
        }

    @app.get("/v1/models")
    async def models(request: Request):
        if (denied := check_auth(request)) is not None:
            return denied
        return {"object": "list", "data": [{"id": spec.alias, "object": "model", "owned_by": "mock-llm"}]}

    @app.api_route("/v1/embeddings", methods=["POST"])
    async def embeddings(request: Request):
        return error_response(
            404, "not_found",
            "No embeddings endpoint (the real course API has none, UNVERIFIED). "
            "Use a local embedding model in code instead.",
        )

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        started = time.monotonic()
        if (denied := check_auth(request)) is not None:
            return denied
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return error_response(400, "invalid_request_error", "Body must be a JSON object.")

        requested_model = body.get("model")
        if requested_model is not None and requested_model != spec.alias:
            return error_response(
                404, "model_not_found",
                f"Model '{requested_model}' not found. This server serves '{spec.alias}'.",
            )

        notes: list[str] = []
        if settings.limits:
            if (rej := limiter.check_rate()) is not None:
                return _reject(rej)
            body, notes, rej = apply_token_budget(body)
            if rej is not None:
                return _reject(rej)
        if (rej := await limiter.acquire()) is not None:
            return _reject(rej)

        body["model"] = backend_model_id
        if settings.log_bodies:
            log.info("request body: %s", json.dumps(body, ensure_ascii=False))
        headers = {"X-Mock-LLM-Notes": "; ".join(notes)} if notes else {}
        client: httpx.AsyncClient = app.state.client

        try:
            if body.get("stream"):
                return await _stream(client, body, headers, started, notes)
            resp = await client.post("chat/completions", json=body)
            try:
                payload = _rewrite_model(resp.json(), spec.alias)
            except ValueError:
                payload = {"error": {"message": resp.text, "type": "backend_error"}}
            if settings.log_bodies:
                log.info("response body: %s", json.dumps(payload, ensure_ascii=False))
            usage = payload.get("usage") if isinstance(payload, dict) else None
            log_line(resp.status_code, started, usage, notes)
            limiter.release()
            return JSONResponse(payload, status_code=resp.status_code, headers=headers)
        except httpx.HTTPError as exc:
            limiter.release()
            log_line(502, started, None, notes)
            return error_response(
                502, "backend_unreachable",
                f"Could not reach backend at {settings.backend_url}: {exc.__class__.__name__}",
            )

    async def _stream(
        client: httpx.AsyncClient, body: dict, headers: dict, started: float, notes: list[str]
    ):
        req = client.build_request("POST", "chat/completions", json=body)
        resp = await client.send(req, stream=True)
        if resp.status_code >= 400:
            raw = await resp.aread()
            await resp.aclose()
            limiter.release()
            log_line(resp.status_code, started, None, notes)
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {"error": {"message": raw.decode(errors="replace"), "type": "backend_error"}}
            return JSONResponse(payload, status_code=resp.status_code, headers=headers)

        async def gen() -> AsyncIterator[str]:
            usage: dict | None = None
            try:
                async for line in resp.aiter_lines():
                    if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                        try:
                            chunk = json.loads(line[5:].strip())
                            _rewrite_model(chunk, spec.alias)
                            usage = chunk.get("usage") or usage
                            line = "data: " + json.dumps(chunk, ensure_ascii=False)
                        except ValueError:
                            pass  # pass unparseable lines through unchanged
                    yield line + "\n"
            finally:
                await resp.aclose()
                limiter.release()
                log_line(resp.status_code, started, usage, notes)

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", **headers}
        )

    return app
