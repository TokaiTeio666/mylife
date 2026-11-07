SHELL := /bin/sh
COMPOSE := docker compose -f infra/compose/docker-compose.yml --env-file .env

.PHONY: help up down logs migrate test test-fast lint fmt api web upgrade

help:
	@echo "mylife Phase 0 targets:"
	@echo "  make up       - 启动全部服务（postgres/redis/minio/api/web/caddy）"
	@echo "  make down     - 停止全部服务"
	@echo "  make logs     - 跟踪日志"
	@echo "  make migrate  - 执行 Alembic 迁移"
	@echo "  make test     - 运行后端测试（需要 PostgreSQL）"
	@echo "  make test-fast- 只跑不依赖数据库的单元测试"
	@echo "  make lint     - ruff + tsc"
	@echo "  make upgrade  - 拉取新镜像并滚动重启（Phase 5 完整实现）"

up:
	$(COMPOSE) up -d --build
	$(COMPOSE) ps

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=200

migrate:
	$(COMPOSE) run --rm migrate

test:
	cd apps/api && uv run pytest -q

test-fast:
	cd apps/api && uv run pytest -q -m "not db"

bench-search:
	MYLIFE_EMBEDDING_PROVIDER=mock uv run python scripts/bench_search.py

bench-import:
	uv run python scripts/bench_import.py

lint:
	cd apps/api && uv run ruff check .
	cd apps/web && npm run lint

fmt:
	cd apps/api && uv run ruff format .

api:
	cd apps/api && uv run python -m mylife_api.devserver

web:
	cd apps/web && npm run dev

upgrade:
	$(COMPOSE) pull && $(COMPOSE) up -d --build
