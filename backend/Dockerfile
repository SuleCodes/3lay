# 3lay backend API image.
#
# Built in Azure (no local Docker needed) and pushed to the container
# registry with:
#   az acr build --registry containerreg3lay --image 3lay-backend:<tag> .
# See README.md -> "Deploying (container)".
#
# All configuration comes from Azure App Configuration at startup; the only
# setting the container needs is the APP_CONFIG_CONNECTION_STRING env var.
# No .env file is baked into the image (see .dockerignore).

# Pinned to 3.11: the same version the dependencies are tested with locally.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code-only changes reuse this cached layer.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# Don't run as root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# --proxy-headers: the app runs behind Azure's HTTPS ingress, so trust its
# X-Forwarded-* headers for the real scheme and client address.
# Migrations are NOT run here -- they're a separate deploy step (README), so
# several replicas starting at once can't race each other on the schema.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
