FROM python:3.12-slim AS base

# 可通过 --build-arg 指向可达的 PyPI 镜像（默认官方源）。
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=180 \
    UV_DEFAULT_INDEX=${PIP_INDEX_URL}

# 用 PyPI 安装 uv，避免额外依赖 ghcr.io 的可达性。
RUN pip install --no-cache-dir --timeout 120 --index-url "${PIP_INDEX_URL}" "uv==0.8.15"

WORKDIR /workspace

# 先复制 workspace 清单与源码。workspace 包需要源码才能构建/安装，
# 因此不能像普通项目那样先 sync 后 COPY。
COPY pyproject.toml ./
COPY packages/contracts packages/contracts
COPY packages/knowledge-runtime packages/knowledge-runtime
COPY packages/retrieval packages/retrieval
COPY packages/agent packages/agent
COPY packages/delivery packages/delivery
COPY apps/api apps/api

RUN uv sync --no-dev --package mylife-api

WORKDIR /workspace/apps/api

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "mylife_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
