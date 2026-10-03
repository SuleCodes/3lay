from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import api_keys, auth

settings = get_settings()

# The schema is managed by Alembic migrations (see migrations/), not created
# at startup. Apply them with `alembic upgrade head` before running the API.

app = FastAPI(title="3lay API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(api_keys.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
