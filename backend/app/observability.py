"""Correlation ids for API requests, and log lines that carry them.

Every request gets one correlation id: the W3C trace id of its trace. Deployed,
ddtrace (started by ddtrace-run, DD_TRACE_ENABLED=true) traces each request and
continues a trace an incoming `traceparent` header started (e.g. the email
Worker's recipient check); the id is that trace's. Without ddtrace (locally,
in tests) it's taken from `traceparent`, or made up, so it exists either way.

It's returned to the caller as the X-Correlation-Id header: the chat shows it
with an error, so a client's "it failed, reference abc..." leads straight to
the trace. The agents use the same scheme (agents/observability.py).
"""

import contextvars
import json
import logging
import os
import re
import secrets
import sys
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

CORRELATION_HEADER = "X-Correlation-Id"
TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")

_correlation_id = contextvars.ContextVar("correlation_id", default=None)


def datadog_enabled() -> bool:
    return os.environ.get("DD_TRACE_ENABLED", "").lower() in ("1", "true")


def _datadog_trace_id() -> str | None:
    if not datadog_enabled():
        return None
    from ddtrace.trace import tracer  # pylint: disable=import-outside-toplevel

    span = tracer.current_root_span()
    return format(span.trace_id, "032x") if span is not None else None


def correlation_id() -> str | None:
    """The current request's correlation id."""
    return _correlation_id.get()


def tag(**tags) -> None:
    """Adds tags (customer id, conversation id...) to the request's trace, for searching."""
    if not datadog_enabled():
        return
    from ddtrace.trace import tracer  # pylint: disable=import-outside-toplevel

    span = tracer.current_root_span()
    if span is not None:
        for key, value in tags.items():
            if value is not None:
                span.set_tag(key, value)


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Gives each request its correlation id and returns it as X-Correlation-Id."""

    async def dispatch(self, request, call_next):
        incoming = TRACEPARENT.match(request.headers.get("traceparent", "").strip().lower())
        value = _datadog_trace_id() or (incoming.group(1) if incoming else secrets.token_hex(16))
        token = _correlation_id.set(value)
        try:
            response = await call_next(request)
        except Exception:  # pylint: disable=broad-exception-caught
            # An error nothing else handled (e.g. a database error). Logged with its
            # stack and correlation id, and answered here, inside the CORS layer, so
            # the browser can read it and show the reference.
            logging.getLogger("3lay.http").exception(
                "Unhandled error on %s %s", request.method, request.url.path)
            response = JSONResponse(status_code=500, content={
                "detail": "Something went wrong on our side. We've logged it."})
        finally:
            _correlation_id.reset(token)
        response.headers[CORRELATION_HEADER] = value
        return response


class RefusedPreflightLogger(BaseHTTPMiddleware):
    """Logs a browser preflight the CORS layer refused. CORS answers those with a
    bare 400 before any endpoint runs, so without this they're invisible."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        origin = request.headers.get("origin")
        if request.method == "OPTIONS" and response.status_code == 400 and origin:
            logging.getLogger("3lay.http").warning(
                "Refused a browser request from origin %s to %s: it isn't an allowed origin "
                "(FRONTEND_URL). Open the app at FRONTEND_URL, or set it to this origin locally.",
                origin, request.url.path)
        return response


class JsonFormatter(logging.Formatter):
    """A log record as one line of JSON. Datadog parses it by itself: "status" is the
    level, and dd.trace_id links the line to its trace (ddtrace fills it in with
    DD_LOGS_INJECTION=true; otherwise the request's correlation id is used)."""

    def format(self, record):
        injected = str(getattr(record, "dd.trace_id", "") or "")
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"),
            "status": record.levelname.lower(),
            "logger.name": record.name,
            "message": record.getMessage(),
            "dd.service": getattr(record, "dd.service", "") or os.environ.get("DD_SERVICE", ""),
            "dd.env": getattr(record, "dd.env", "") or os.environ.get("DD_ENV", ""),
            "dd.version": getattr(record, "dd.version", "") or os.environ.get("DD_VERSION", ""),
            "dd.trace_id": injected if injected not in ("", "0") else (correlation_id() or "0"),
            "dd.span_id": str(getattr(record, "dd.span_id", "") or "0"),
        }
        if record.exc_info:
            entry["error.kind"] = record.exc_info[0].__name__
            entry["error.message"] = str(record.exc_info[1])
            entry["error.stack"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


# The app's own loggers, and uvicorn's (which would otherwise write plain text
# to stderr, all counted as errors by Datadog).
APP_LOGGERS = ("3lay", "uvicorn", "uvicorn.error", "uvicorn.access")
NOISY_LOGGERS = ("azure", "urllib3")  # every storage/App Configuration HTTP request at INFO


def configure_logging() -> None:
    """JSON logs on stdout, with correlation ids. Called when the app is imported,
    which is after uvicorn has set up its own logging, so this replaces it."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    for name in APP_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(logging.INFO)
        logger.propagate = False
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
