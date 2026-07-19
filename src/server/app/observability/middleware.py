from time import perf_counter
from typing import Any
from uuid import uuid4

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.observability.metrics import HTTP_DURATION, HTTP_REQUESTS


class ObservabilityMiddleware:
    """Add request IDs, structured access logs and bounded-label HTTP metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = perf_counter()
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        method = str(scope.get("method", "UNKNOWN"))
        status_code = 500
        structlog.contextvars.bind_contextvars(request_id=request_id)

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            duration = perf_counter() - started_at
            route = self._route_template(scope)
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status_code)).inc()
            HTTP_DURATION.labels(method=method, route=route).observe(duration)
            structlog.get_logger("http.access").info(
                "request_completed",
                method=method,
                route=route,
                status_code=status_code,
                duration_ms=round(duration * 1000, 3),
            )
            structlog.contextvars.clear_contextvars()

    @staticmethod
    def _route_template(scope: Scope) -> str:
        route: Any = scope.get("route")
        return str(getattr(route, "path", "unmatched"))
