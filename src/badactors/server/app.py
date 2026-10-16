"""FastAPI application entry point."""

from __future__ import annotations

import hmac
import os
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response


def create_app() -> FastAPI:
    api = FastAPI(title="Bad Actors", version="0.3.0")

    @api.middleware("http")
    async def owner_access(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = os.getenv("BADACTORS_API_TOKEN")
        if request.url.path == "/health":
            return await call_next(request)
        if token:
            supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, token):
                return JSONResponse(
                    {"detail": "Owner token required."}, status_code=401
                )
        elif request.client is None or request.client.host not in {
            "127.0.0.1",
            "::1",
            "testclient",
        }:
            return JSONResponse(
                {"detail": "Configure BADACTORS_API_TOKEN for remote access."},
                status_code=403,
            )
        elif request.url.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "testserver",
        }:
            return JSONResponse(
                {"detail": "A loopback hostname is required without an owner token."},
                status_code=403,
            )
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname != request.url.hostname:
                return JSONResponse(
                    {"detail": "Cross-origin writes are disabled."}, status_code=403
                )
        return await call_next(request)

    @api.get("/health")
    async def health() -> dict[str, object]:
        return {"status": "ok"}

    return api


app = create_app()
