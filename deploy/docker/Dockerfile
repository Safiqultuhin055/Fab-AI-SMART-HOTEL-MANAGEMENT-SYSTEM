# =============================================================================
# ASHOS application image.
#
# Multi-stage: wheels are built in a fat stage and copied into a slim runtime,
# so the shipped image carries no compiler toolchain.
#
# BUILD ARG `EXTRAS` selects the dependency set:
#   base  — web + worker nodes that call remote AI APIs   (~450 MB)
#   ai    — nodes running local inference (torch, CLIP…)  (~6 GB)
# Splitting this keeps the web tier deployable in seconds instead of minutes.
# =============================================================================

ARG PYTHON_VERSION=3.12-slim-bookworm

# --- builder -----------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS builder

ARG EXTRAS=base

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements/ ./requirements/
RUN pip wheel --wheel-dir=/wheels/dist -r requirements/${EXTRAS}.txt


# --- runtime -----------------------------------------------------------------
FROM python:${PYTHON_VERSION} AS runtime

ARG EXTRAS=base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

# libpq for psycopg, libgl/libglib for OpenCV when EXTRAS=ai, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        gettext \
    && rm -rf /var/lib/apt/lists/*

# Non-root from the start: a container running as root is one file-upload bug
# away from being a much worse day.
RUN groupadd --gid 1000 ashos && useradd --uid 1000 --gid ashos --create-home ashos

WORKDIR /app

COPY --from=builder /wheels/dist /wheels/dist
COPY requirements/ ./requirements/
RUN pip install --no-index --find-links=/wheels/dist -r requirements/${EXTRAS}.txt \
    && rm -rf /wheels

COPY --chown=ashos:ashos . .

RUN mkdir -p /app/staticfiles /app/media /app/ai_models \
    && chown -R ashos:ashos /app/staticfiles /app/media /app/ai_models

COPY --chown=ashos:ashos deploy/docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER ashos

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["web"]
