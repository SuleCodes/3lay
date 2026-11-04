"""Tracing: one correlation id for everything an email or a request causes.

The correlation id is the W3C trace id (32 hex characters). It starts at the
first entry point and is handed on at every hop:

    email:  Cloudflare Worker (starts it) --traceparent header--> ingest Function
            --"traceparent" in the queue message--> this job --> Orchy, Obed, webhook
    web:    browser --> API (starts one per request) --> Boardy, its model calls, the database

and it's stored on the event record (events.trace_id), so an event, a log line
or a webhook can be followed back to the whole trace in Datadog.

Deployed, the job and the API run under ddtrace (Datadog's tracer, started by
ddtrace-run with DD_TRACE_ENABLED=true): it records the spans (LangGraph nodes,
model calls, HTTP, SQL) and sends them to Datadog. Without it (locally, in
tests), this module still keeps the correlation id, from the traceparent alone,
so logs and records carry it either way.

W3C Trace Context: https://www.w3.org/TR/trace-context/
"""

import contextvars
import json
import logging
import os
import re
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime, timezone

TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_trace_id = contextvars.ContextVar("trace_id", default=None)


def datadog_enabled():
    """True when running under ddtrace with tracing on (the deployed containers)."""
    return os.environ.get("DD_TRACE_ENABLED", "").lower() in ("1", "true")


def new_traceparent():
    """A traceparent for a new trace: "00-<trace id>-<span id>-01" (sampled)."""
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def parse_traceparent(value):
    """(trace id, parent span id) from a traceparent header, or None if it isn't one."""
    match = TRACEPARENT.match((value or "").strip().lower())
    if not match or set(match.group(1)) == {"0"} or set(match.group(2)) == {"0"}:
        return None
    return match.group(1), match.group(2)


def current_trace_id():
    """The correlation id of whatever is running now, or None outside a trace."""
    if datadog_enabled():
        from ddtrace.trace import tracer  # pylint: disable=import-outside-toplevel
        span = tracer.current_span()
        if span is not None:
            return format(span.trace_id, "032x")
    return _trace_id.get()


def tag(**tags):
    """Adds tags (e.g. event_id, customer id) to the current span, so traces can be searched."""
    if not datadog_enabled():
        return
    from ddtrace.trace import tracer  # pylint: disable=import-outside-toplevel
    span = tracer.current_span()
    if span is not None:
        for key, value in tags.items():
            if value is not None:
                span.set_tag(key, value)


@contextmanager
def continue_trace(traceparent, name, resource=None, **tags):
    """A span continuing the trace a traceparent came from (or a new trace if none).

    Use it where work arrives without HTTP headers, e.g. a queue message. Yields
    the correlation id.
    """
    parsed = parse_traceparent(traceparent)
    if not datadog_enabled():
        token = _trace_id.set(parsed[0] if parsed else secrets.token_hex(16))
        try:
            yield _trace_id.get()
        finally:
            _trace_id.reset(token)
        return

    # pylint: disable=import-outside-toplevel
    from ddtrace.propagation.http import HTTPPropagator
    from ddtrace.trace import tracer
    parent = HTTPPropagator.extract({"traceparent": traceparent}) if parsed else None
    # An explicit parent (not activating it), so that when this span ends nothing
    # of this trace stays active: the job's next message starts clean. No
    # parent: a new trace.
    span = tracer.start_span(name, child_of=parent if parent and parent.trace_id else None,
                             resource=resource or name, activate=True)
    for key, value in tags.items():
        if value is not None:
            span.set_tag(key, value)
    token = _trace_id.set(format(span.trace_id, "032x"))
    try:
        yield _trace_id.get()
    except BaseException as exc:
        span.set_exc_info(type(exc), exc, exc.__traceback__)
        raise
    finally:
        _trace_id.reset(token)
        span.finish()


# Logs: one JSON object per line on stdout. Datadog parses JSON logs by itself:
# "status" is the level (plain text on stderr would all count as errors), and
# dd.trace_id / dd.span_id link each line to its trace. ddtrace fills those in
# (DD_LOGS_INJECTION=true); without ddtrace, the correlation id is used.
NOISY_LOGGERS = ("azure", "urllib3")  # every storage HTTP request at INFO: warnings only


class JsonFormatter(logging.Formatter):
    """A log record as one line of JSON, with Datadog's standard attribute names."""

    def format(self, record):
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(
                timespec="milliseconds"),
            "status": record.levelname.lower(),
            "logger.name": record.name,
            "message": record.getMessage(),
            "dd.service": getattr(record, "dd.service", "") or os.environ.get("DD_SERVICE", ""),
            "dd.env": getattr(record, "dd.env", "") or os.environ.get("DD_ENV", ""),
            "dd.version": getattr(record, "dd.version", "") or os.environ.get("DD_VERSION", ""),
            "dd.trace_id": _record_trace_id(record),
            "dd.span_id": str(getattr(record, "dd.span_id", "") or "0"),
        }
        if record.exc_info:
            entry["error.kind"] = record.exc_info[0].__name__
            entry["error.message"] = str(record.exc_info[1])
            entry["error.stack"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def _record_trace_id(record):
    injected = str(getattr(record, "dd.trace_id", "") or "")
    return injected if injected not in ("", "0") else (current_trace_id() or "0")


def configure_logging(level="INFO"):
    """JSON logs on stdout (Datadog collects the containers' output), with correlation ids."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
