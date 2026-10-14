# 3lay backend API image.
#
# Built in Azure (no local Docker needed) from the REPO ROOT, because the API
# runs Boardy, the onboarding agent, from the agents package:
#   az acr build --registry containerreg3lay --image 3lay-backend:<tag> -f backend/Dockerfile .
# See README.md -> "Deploying (container)". The root .dockerignore decides
# what's uploaded (never agents/tmp/ or any .env).
#
# All configuration comes from Azure App Configuration at startup (BACKEND:*,
# and AGENTS:* for Boardy); the only setting the container needs is the
# APP_CONFIG_CONNECTION_STRING env var. No .env file is baked into the image.

# Pinned to 3.11: the same version the dependencies are tested with locally.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AGENTS_DIR=/agents

WORKDIR /app

# Dependencies first, so code-only changes reuse this cached layer.
# backend/requirements.txt includes ../agents/requirements.txt, i.e. /agents/.
COPY agents/requirements.txt /agents/requirements.txt
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/app ./app
COPY agents /agents

# Don't run as root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# --proxy-headers: the app runs behind Azure's HTTPS ingress, so trust its
# X-Forwarded-* headers for the real scheme and client address.
# Migrations are NOT run here -- they're a separate deploy step (README), so
# several replicas starting at once can't race each other on the schema.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
