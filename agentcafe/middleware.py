"""Request-scoped middleware for AgentCafe."""

from __future__ import annotations

import contextvars
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign a unique request ID to every incoming request.

    - If the client sends ``X-Request-ID``, that value is preserved.
    - Otherwise a UUID4 is generated.
    - The ID is stored in a ``contextvars.ContextVar`` so loggers and handlers
      can access it without plumbing it through every function signature.
    - The ID is echoed back in the ``X-Request-ID`` response header.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_var.reset(token)
