#!/bin/bash
# 创建最小权限角色。由 postgres 官方镜像的 init 机制执行（仅首次初始化数据卷时）。
set -euo pipefail

APP_PASSWORD="${MYLIFE_APP_DB_PASSWORD:-change-me-app}"
SYSTEM_PASSWORD="${MYLIFE_SYSTEM_DB_PASSWORD:-change-me-system}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_app') THEN
      CREATE ROLE mylife_app LOGIN NOINHERIT NOBYPASSRLS PASSWORD '${APP_PASSWORD}';
    ELSE
      ALTER ROLE mylife_app WITH LOGIN NOINHERIT NOBYPASSRLS PASSWORD '${APP_PASSWORD}';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mylife_system') THEN
      CREATE ROLE mylife_system LOGIN NOINHERIT NOBYPASSRLS PASSWORD '${SYSTEM_PASSWORD}';
    ELSE
      ALTER ROLE mylife_system WITH LOGIN NOINHERIT NOBYPASSRLS PASSWORD '${SYSTEM_PASSWORD}';
    END IF;
  END \$\$;
EOSQL

echo "mylife roles initialized"
