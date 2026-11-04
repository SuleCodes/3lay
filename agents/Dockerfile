# 3lay agents image: the ingest job (Orchy, Obed, the rule engine and Noti).
#
# Built in Azure (no local Docker needed) and pushed to the container
# registry with:
#   az acr build --registry containerreg3lay --image 3lay-agents:<tag> .
# See DEPLOY.md.
#
# All configuration comes from Azure App Configuration (AGENTS:* keys) at
# startup; the container needs only APP_CONFIG_CONNECTION_STRING (a Container
# Apps secret) and AZURE_CLIENT_ID (the job's managed identity, for Key Vault
# and storage). No .env file is baked into the image (see .dockerignore).

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

COPY settings.py observability.py ./
COPY orchy ./orchy
COPY obed ./obed
COPY rules ./rules
COPY noti ./noti
COPY store ./store
COPY jobs ./jobs

# Datadog: serverless-init wraps the process and sends its traces, logs and
# metrics to Datadog; ddtrace-run turns on tracing (FastAPI, httpx, SQL,
# LangChain/LangGraph and LLM Observability). Both are configured by the
# container's DD_* environment variables (see the README's "Observability");
# with DD_TRACE_ENABLED unset, nothing is traced and the app runs as before.
COPY --from=datadog/serverless-init:1 /datadog-init /app/datadog-init

# Don't run as root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# One execution: process the waiting queue messages, then exit.
ENTRYPOINT ["/app/datadog-init"]
CMD ["ddtrace-run", "python", "-m", "jobs.ingest"]
