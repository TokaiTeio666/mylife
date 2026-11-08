#!/bin/sh
# PostgreSQL 备份（AC-61 / SEC-28）。
#
# 产物：{BACKUP_DIR}/pg/{YYYYmmddTHHMMSSZ}.dump(+.sha256) 或 .age/.enc
#   - 每日一份，保留 7 天；每月 1 日额外打 monthly 标记保留 30 天。
#   - 目录 0700、文件 0600；设置 MYLIFE_BACKUP_PASSPHRASE 时加密，
#     恢复需要同一口令（SEC-28「恢复需密钥」）。
#
# 用法：MYLIFE_DB_HOST=localhost MYLIFE_DB_PORT=25432 ./scripts/backup_pg.sh
set -eu

BACKUP_DIR="${MYLIFE_BACKUP_DIR:-./data/backups}"
DB_HOST="${MYLIFE_DB_HOST:-localhost}"
DB_PORT="${MYLIFE_DB_PORT:-5432}"
DB_NAME="${MYLIFE_DB_NAME:-mylife}"
DB_USER="${POSTGRES_USER:-mylife_migrator}"
DB_PASSWORD="${POSTGRES_PASSWORD:-change-me-migrator}"
PASSPHRASE="${MYLIFE_BACKUP_PASSPHRASE:-}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DAY="$(date -u +%d)"
OUT_DIR="${BACKUP_DIR}/pg"
mkdir -p "$OUT_DIR"
chmod 700 "$BACKUP_DIR" "$OUT_DIR"

SUFFIX=""
[ "$DAY" = "01" ] && SUFFIX="-monthly"
BASE="${OUT_DIR}/${STAMP}${SUFFIX}"
TMP="${OUT_DIR}/.${STAMP}.dump.tmp"

dump() {
  if command -v pg_dump >/dev/null 2>&1; then
    PGPASSWORD="$DB_PASSWORD" pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" \
      -d "$DB_NAME" -Fc -f "$1"
    return
  fi
  # 回退：在 compose 的 postgres 容器里执行，再把产物拷出来。
  COMPOSE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
  docker compose \
    -f "$COMPOSE_ROOT/infra/compose/docker-compose.yml" \
    -f "$COMPOSE_ROOT/infra/compose/docker-compose.dev.yml" \
    exec -T -e "PGPASSWORD=$DB_PASSWORD" postgres \
    pg_dump -h postgres -p 5432 -U "$DB_USER" -d "$DB_NAME" -Fc > "$1"
}

dump "$TMP"

if [ -n "$PASSPHRASE" ]; then
  if command -v age >/dev/null 2>&1; then
    printf '%s\n' "$PASSPHRASE" | age -p -o "${BASE}.dump.age" "$TMP"
    ARTIFACT="${BASE}.dump.age"
  elif command -v openssl >/dev/null 2>&1; then
    openssl enc -aes-256-ctr -pbkdf2 -salt -pass pass:"$PASSPHRASE" \
      -in "$TMP" -out "${BASE}.dump.enc"
    ARTIFACT="${BASE}.dump.enc"
  else
    rm -f "$TMP"
    echo "no encryptor available (age/openssl) but a passphrase is set" >&2
    exit 1
  fi
  rm -f "$TMP"
else
  mv "$TMP" "${BASE}.dump"
  ARTIFACT="${BASE}.dump"
fi

chmod 600 "$ARTIFACT"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$ARTIFACT" > "${ARTIFACT}.sha256"
else
  shasum -a 256 "$ARTIFACT" > "${ARTIFACT}.sha256"
fi
chmod 600 "${ARTIFACT}.sha256"

# 保留：7 天常规；monthly 保留 30 天。
find "$OUT_DIR" -type f -name '*.dump*' -mtime +7 ! -name '*monthly*' -delete 2>/dev/null || true
find "$OUT_DIR" -type f -name '*monthly*' -mtime +30 -delete 2>/dev/null || true

echo "backup: $ARTIFACT"
