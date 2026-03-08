"""
RapidRAG — middleware.py
Request/response middleware for:
- Request ID tracking (every request gets a unique ID)
- Latency measurement (logged per request)
- Structured error handling (RapidRAGError → proper HTTP responses)
- Simple rate limiting (per-client, in-memory)
"""

import time
import uuid
import logging
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings
from errors import RapidRAGError

logger = logging.getLogger("rapidrag.middleware")


# ─────────────────────────────────────────────
# Request Tracking & Latency Middleware
# ─────────────────────────────────────────────

class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """
    Adds to every request:
    - X-Request-ID header (unique per request, for tracing)
    - X-Response-Time header (latency in ms)
    - Structured logging of each request
    """

    async def dispatch(self, request: Request, call_next):
        # Generate request ID
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # Extract client_id from path if present
        client_id = self._extract_client_id(request.url.path)

        start_time = time.time()

        try:
            response = await call_next(request)
        except Exception as exc:
            # Handle RapidRAGError with proper status codes
            if isinstance(exc, RapidRAGError):
                latency_ms = int((time.time() - start_time) * 1000)
                logger.warning(
                    f"[{request_id}] {exc.__class__.__name__}: {exc.detail} "
                    f"({latency_ms}ms)"
                )
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail, "request_id": request_id},
                    headers={
                        "X-Request-ID": request_id,
                        "X-Response-Time": str(latency_ms),
                    },
                )
            raise

        latency_ms = int((time.time() - start_time) * 1000)

        # Add tracking headers to response
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time"] = f"{latency_ms}ms"

        # Log request (skip health checks to reduce noise)
        if "/health" not in request.url.path:
            log_level = logging.WARNING if latency_ms > 3000 else logging.INFO
            logger.log(
                log_level,
                f"[{request_id}] {request.method} {request.url.path} "
                f"→ {response.status_code} ({latency_ms}ms)"
                f"{f' client={client_id}' if client_id else ''}"
            )

        return response

    @staticmethod
    def _extract_client_id(path: str) -> str | None:
        """Extract client_id from URL path: /api/v1/{client_id}/..."""
        parts = path.strip("/").split("/")
        # Pattern: api/v1/{client_id}/...
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
            # Skip 'clients' endpoint
            if parts[2] != "clients":
                return parts[2]
        return None


# ─────────────────────────────────────────────
# Rate Limiting Middleware
# ─────────────────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple in-memory per-client rate limiter.
    Sliding window: N requests per minute per client_id.
    Lightweight — no Redis needed for small scale.
    """

    def __init__(self, app, max_requests: int | None = None):
        super().__init__(app)
        self.max_requests = max_requests or settings.rate_limit_per_minute
        self.window_seconds = 60
        # {client_id: [timestamp, timestamp, ...]}
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Only rate limit API endpoints
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_id = RequestTrackingMiddleware._extract_client_id(
            request.url.path
        )
        if not client_id:
            return await call_next(request)

        # Check rate limit
        now = time.time()
        window_start = now - self.window_seconds

        # Clean old entries for this client
        self._requests[client_id] = [
            t for t in self._requests[client_id] if t > window_start
        ]

        # Periodically prune stale clients (every 60 seconds)
        last_prune = getattr(self, '_last_prune', 0)
        if now - last_prune > 60:
            self._last_prune = now
            stale_cutoff = now - 300  # 5 minutes
            stale_keys = [
                k for k, v in self._requests.items()
                if not v or v[-1] < stale_cutoff
            ]
            for k in stale_keys:
                del self._requests[k]

        if len(self._requests[client_id]) >= self.max_requests:
            logger.warning(
                f"Rate limit exceeded for client: {client_id} "
                f"({len(self._requests[client_id])}/{self.max_requests} per min)"
            )
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                    "retry_after_seconds": int(
                        self._requests[client_id][0]
                        - window_start
                        + 1
                    ),
                },
                headers={"Retry-After": "60"},
            )

        # Record this request
        self._requests[client_id].append(now)

        return await call_next(request)


# ─────────────────────────────────────────────
# Error Handler for RapidRAGError
# ─────────────────────────────────────────────

async def rapidrag_error_handler(request: Request, exc: RapidRAGError):
    """FastAPI exception handler for all RapidRAGError subclasses."""
    request_id = getattr(request.state, "request_id", "unknown")

    logger.error(
        f"[{request_id}] {exc.__class__.__name__}: {exc.detail}"
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "error_type": exc.__class__.__name__,
            "request_id": request_id,
        },
    )
