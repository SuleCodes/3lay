from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.observability import (CORRELATION_HEADER, CorrelationMiddleware, RefusedPreflightLogger,
                               configure_logging)
from app.routers import account, api_keys, auth, boardy, events, internal, webhooks

settings = get_settings()
configure_logging()

# The schema is managed by Alembic migrations (see migrations/), not created
# at startup. Apply them with `alembic upgrade head` before running the API.

app = FastAPI(title="3lay API", version="0.1.0")

# Middleware runs outside-in in the reverse of the order added: the last one
# added sees a request first. So, from the outside: RefusedPreflightLogger,
# CORS, CorrelationMiddleware. CORS outside the correlation middleware means
# every response it makes, including a 500 for an unexpected error, gets the
# CORS headers, so the browser can read it.

# Every request's correlation id (its trace id), returned as X-Correlation-Id;
# turns unexpected errors into a readable 500.
app.add_middleware(CorrelationMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # So the frontend can read it and show it with an error.
    expose_headers=[CORRELATION_HEADER],
)
app.add_middleware(RefusedPreflightLogger)

app.include_router(auth.router)
app.include_router(account.router)
app.include_router(api_keys.router)
app.include_router(internal.router)
app.include_router(boardy.router)
app.include_router(webhooks.router)
app.include_router(events.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
