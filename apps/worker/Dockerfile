FROM python:3.12-slim

# 可通过 --build-arg 指向可达的 PyPI 镜像（默认官方源）。
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180 \
    UV_DEFAULT_INDEX=${PIP_INDEX_URL}

RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --timeout 120 --index-url "${PIP_INDEX_URL}" "uv==0.8.15"

WORKDIR /workspace

COPY pyproject.toml ./
COPY packages/contracts packages/contracts
COPY packages/knowledge-runtime packages/knowledge-runtime
COPY packages/retrieval packages/retrieval
COPY apps/worker apps/worker

RUN uv sync --no-dev --package mylife-worker

WORKDIR /workspace/apps/worker

CMD ["uv", "run", "--no-sync", "celery", "-A", "mylife_worker.celery_app", "worker", "--loglevel=INFO", "-Q", "q.ingest", "--concurrency=4"]
