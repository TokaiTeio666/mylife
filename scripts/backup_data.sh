#!/bin/sh
# 数据根备份（AC-61 / SEC-28 / PHASE5-SPEC §9 & D23）。
#
# 本机对象存储未启用（交付与导出均落盘于 MYLIFE_DATA_ROOT/workspaces），
# 因此对数据根做 tar 归档，并在清单中显式标注 minio: not_applicable。
#   - 每日一份，保留 7 天；每月 1 日额外打 monthly 标记保留 30 天。
#   - 目录 0700、文件 0600；设置 MYLIFE_BACKUP_PASSPHRASE 时加密（age/openssl）。
set -eu

DATA_ROOT="${MYLIFE_DATA_ROOT:-./data}"
BACKUP_DIR="${MYLIFE_BACKUP_DIR:-./data/backups}"
PASSPHRASE="${MYLIFE_BACKUP_PASSPHRASE:-}"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DAY="$(date -u +%d)"
OUT_DIR="${BACKUP_DIR}/data"
mkdir -p "$OUT_DIR"
chmod 700 "$BACKUP_DIR" "$OUT_DIR"

SUFFIX=""
[ "$DAY" = "01" ] && SUFFIX="-monthly"
BASE="${OUT_DIR}/${STAMP}${SUFFIX}"
TMP="${OUT_DIR}/.${STAMP}.tar.gz.tmp"

WORKSPACES_DIR="${DATA_ROOT}/workspaces"
mkdir -p "$WORKSPACES_DIR"
FILE_COUNT="$(find "$WORKSPACES_DIR" -type f 2>/dev/null | wc -l | tr -d ' ')"

# 通过标准输出重定向写入目标路径，避免 GNU tar 在 Windows Git Bash 下把 `C:/...` 误判为远端主机。
(cd "$DATA_ROOT" && tar -czf - workspaces) > "$TMP"

if [ -n "$PASSPHRASE" ]; then
  if command -v age >/dev/null 2>&1; then
    printf '%s\n' "$PASSPHRASE" | age -p -o "${BASE}.tar.gz.age" "$TMP"
    ARTIFACT="${BASE}.tar.gz.age"
  elif command -v openssl >/dev/null 2>&1; then
    openssl enc -aes-256-ctr -pbkdf2 -salt -pass pass:"$PASSPHRASE" \
      -in "$TMP" -out "${BASE}.tar.gz.enc"
    ARTIFACT="${BASE}.tar.gz.enc"
  else
    rm -f "$TMP"
    echo "no encryptor available (age/openssl) but a passphrase is set" >&2
    exit 1
  fi
  rm -f "$TMP"
else
  mv "$TMP" "${BASE}.tar.gz"
  ARTIFACT="${BASE}.tar.gz"
fi

chmod 600 "$ARTIFACT"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$ARTIFACT" > "${ARTIFACT}.sha256"
else
  shasum -a 256 "$ARTIFACT" > "${ARTIFACT}.sha256"
fi
chmod 600 "${ARTIFACT}.sha256"

cat > "${ARTIFACT}.meta.json" <<EOF
{
  "created_at": "${STAMP}",
  "artifact": "$(basename "$ARTIFACT")",
  "workspace_files": ${FILE_COUNT},
  "minio": "not_applicable",
  "encrypted": $([ -n "$PASSPHRASE" ] && echo true || echo false)
}
EOF
chmod 600 "${ARTIFACT}.meta.json"

# 保留：7 天常规；monthly 保留 30 天。
find "$OUT_DIR" -type f -name '*.tar.gz*' -mtime +7 ! -name '*monthly*' -delete 2>/dev/null || true
find "$OUT_DIR" -type f -name '*monthly*' -mtime +30 -delete 2>/dev/null || true

echo "data-backup: $ARTIFACT"
