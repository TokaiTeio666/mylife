FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# 用 PyPI 安装 uv，避免额外依赖 ghcr.io 的可达性。
RUN pip install --no-cache-dir "uv==0.8.15"

WORKDIR /workspace

# 先复制 workspace 清单与源码。workspace 包需要源码才能构建/安装，
# 因此不能像普通项目那样先 sync 后 COPY。
COPY pyproject.toml ./
COPY packages/contracts packages/contracts
COPY apps/api apps/api

RUN uv sync --no-dev --package mylife-api

WORKDIR /workspace/apps/api

EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "mylife_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
