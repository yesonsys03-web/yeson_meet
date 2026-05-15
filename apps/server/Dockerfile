FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy workspace manifests + lockfile for reproducible dependency resolution
COPY pyproject.toml uv.lock ./
COPY apps/server/pyproject.toml ./apps/server/pyproject.toml
COPY apps/client_sidecar/pyproject.toml ./apps/client_sidecar/pyproject.toml
COPY packages/sdk-python/pyproject.toml ./packages/sdk-python/pyproject.toml

# Pre-create package skeletons so uv workspace resolves
RUN mkdir -p apps/client_sidecar apps/server packages/sdk-python/yeson_meet_sdk \
    && touch apps/client_sidecar/__init__.py \
    && touch packages/sdk-python/yeson_meet_sdk/__init__.py

RUN uv sync --frozen --no-install-project --package yeson-meet-server

# Copy server code
COPY apps/server ./apps/server

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "apps.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
