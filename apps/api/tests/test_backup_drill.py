"""备份加密、校验和与保留档位验收（AC-61 / SEC-28）。

断言要点：
- 设置 `MYLIFE_BACKUP_PASSPHRASE` 时，`backup_pg.sh` 与 `backup_data.sh` 产出加密文件；
- 加密产物不含明文自定义 dump 魔数 `PGDMP`，也不含原文片段；
- 错误口令无法解密，正确口令解密后头字节为 `PGDMP`（恢复需密钥，SEC-28）；
- `.sha256` 校验和与归档字节逐位一致；
- 7 天常规与 30 天 monthly 两档保留策略不误删 monthly 归档。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _find_sh() -> str | None:
    for candidate in (
        shutil.which("sh"),
        r"C:\Program Files\Git\bin\sh.exe",
        r"C:\Program Files\Git\usr\bin\sh.exe",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _find_openssl(sh_bin: str) -> str | None:
    found = shutil.which("openssl")
    if found:
        return found
    git_openssl = Path(sh_bin).resolve().parents[1] / "usr" / "bin" / "openssl.exe"
    if git_openssl.exists():
        return str(git_openssl)
    return None


def test_encrypted_data_backup_requires_passphrase_and_preserves_monthly(tmp_path):
    sh_bin = _find_sh()
    if sh_bin is None:
        pytest.skip("当前环境无 POSIX sh")
    openssl_bin = _find_openssl(sh_bin)
    if openssl_bin is None and shutil.which("age") is None:
        pytest.skip("当前环境无 age 或 openssl")

    data_root = tmp_path / "data"
    ws_dir = data_root / "workspaces" / "ws-sec28"
    ws_dir.mkdir(parents=True)
    secret_note = "绝密备份明文-SEC28-987654321"
    (ws_dir / "note.md").write_text(secret_note, encoding="utf-8")

    backup_dir = tmp_path / "backups"
    out_dir = backup_dir / "data"
    out_dir.mkdir(parents=True)

    # 预置两个旧归档：10 天前常规归档应被清理，10 天前 monthly 归档应保留（30 天档）。
    stale_regular = out_dir / "20261001T000000Z.tar.gz.enc"
    stale_monthly = out_dir / "20261001T000000Z-monthly.tar.gz.enc"
    stale_regular.write_bytes(b"old-regular")
    stale_monthly.write_bytes(b"old-monthly")
    ten_days_ago = time.time() - 10 * 86400
    os.utime(stale_regular, (ten_days_ago, ten_days_ago))
    os.utime(stale_monthly, (ten_days_ago, ten_days_ago))

    env = dict(os.environ)
    if openssl_bin:
        env["PATH"] = str(Path(openssl_bin).parent) + os.pathsep + env.get("PATH", "")
    env["MYLIFE_DATA_ROOT"] = data_root.as_posix()
    env["MYLIFE_BACKUP_DIR"] = backup_dir.as_posix()
    env["MYLIFE_BACKUP_PASSPHRASE"] = "sec28-passphrase-correct"

    completed = subprocess.run(
        [sh_bin, (ROOT / "scripts" / "backup_data.sh").as_posix()],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "data-backup:" in completed.stdout

    # 10 天前的常规归档被删，monthly 保留
    assert not stale_regular.exists(), "超过 7 天的常规备份应被清理"
    assert stale_monthly.exists(), "未满 30 天的 monthly 备份必须保留"

    artifacts = [
        p
        for p in out_dir.iterdir()
        if p.name != stale_monthly.name
        and (p.name.endswith(".tar.gz.enc") or p.name.endswith(".tar.gz.age"))
    ]
    assert len(artifacts) == 1
    artifact = artifacts[0]
    raw = artifact.read_bytes()
    assert secret_note.encode("utf-8") not in raw
    # gzip 魔数 1f 8b 不应直接暴露在头部
    assert not raw.startswith(b"\x1f\x8b")

    checksum_line = Path(f"{artifact}.sha256").read_text(encoding="utf-8").strip()
    recorded_sha = checksum_line.split()[0]
    assert recorded_sha == hashlib.sha256(raw).hexdigest()

    meta = json.loads(Path(f"{artifact}.meta.json").read_text(encoding="utf-8"))
    assert meta["minio"] == "not_applicable"
    assert meta["encrypted"] is True
    assert meta["workspace_files"] == 1

    if artifact.name.endswith(".enc") and openssl_bin:
        wrong_out = tmp_path / "wrong.tar.gz"
        wrong = subprocess.run(
            [
                openssl_bin,
                "enc",
                "-d",
                "-aes-256-ctr",
                "-pbkdf2",
                "-pass",
                "pass:wrong-passphrase",
                "-in",
                str(artifact),
                "-out",
                str(wrong_out),
            ],
            capture_output=True,
        )
        # AES-CTR 无认证标签，错误密钥解密出的字节必不是合法 gzip tar
        assert wrong.returncode != 0 or not tarfile.is_tarfile(wrong_out)

        good_out = tmp_path / "good.tar.gz"
        subprocess.run(
            [
                openssl_bin,
                "enc",
                "-d",
                "-aes-256-ctr",
                "-pbkdf2",
                "-pass",
                "pass:sec28-passphrase-correct",
                "-in",
                str(artifact),
                "-out",
                str(good_out),
            ],
            check=True,
            capture_output=True,
        )
        with tarfile.open(good_out, "r:gz") as tar:
            extracted = tar.extractfile("workspaces/ws-sec28/note.md")
            assert extracted is not None
            assert extracted.read().decode("utf-8") == secret_note


@pytest.mark.db
def test_encrypted_pg_backup_requires_passphrase(tmp_path):
    sh_bin = _find_sh()
    if sh_bin is None:
        pytest.skip("当前环境无 POSIX sh")
    openssl_bin = _find_openssl(sh_bin)
    if openssl_bin is None and shutil.which("age") is None:
        pytest.skip("当前环境无 age 或 openssl")

    backup_dir = tmp_path / "pg_backups"
    env = dict(os.environ)
    if openssl_bin:
        env["PATH"] = str(Path(openssl_bin).parent) + os.pathsep + env.get("PATH", "")
    env["MYLIFE_BACKUP_DIR"] = backup_dir.as_posix()
    env["MYLIFE_DB_HOST"] = os.environ.get("MYLIFE_TEST_DB_HOST", "localhost")
    env["MYLIFE_DB_PORT"] = os.environ.get("MYLIFE_TEST_DB_PORT", "25432")
    env["MYLIFE_DB_NAME"] = os.environ.get("MYLIFE_TEST_DB_NAME", "mylife_test")
    env["POSTGRES_USER"] = "mylife_migrator"
    env["POSTGRES_PASSWORD"] = "change-me-migrator"
    env["MYLIFE_BACKUP_PASSPHRASE"] = "pg-sec28-secret"

    completed = subprocess.run(
        [sh_bin, (ROOT / "scripts" / "backup_pg.sh").as_posix()],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert "backup:" in completed.stdout

    pg_dir = backup_dir / "pg"
    dumps = [
        p
        for p in pg_dir.iterdir()
        if p.name.endswith(".dump.enc") or p.name.endswith(".dump.age")
    ]
    assert len(dumps) == 1
    encrypted = dumps[0]
    raw = encrypted.read_bytes()
    assert not raw.startswith(b"PGDMP"), "加密后的备份不得直接暴露 PGDMP 头"

    sha_file = Path(f"{encrypted}.sha256")
    assert sha_file.read_text(encoding="utf-8").split()[0] == hashlib.sha256(raw).hexdigest()

    if encrypted.name.endswith(".enc") and openssl_bin:
        wrong_dump = tmp_path / "wrong.dump"
        subprocess.run(
            [
                openssl_bin,
                "enc",
                "-d",
                "-aes-256-ctr",
                "-pbkdf2",
                "-pass",
                "pass:wrong-key",
                "-in",
                str(encrypted),
                "-out",
                str(wrong_dump),
            ],
            capture_output=True,
        )
        assert not wrong_dump.exists() or not wrong_dump.read_bytes().startswith(b"PGDMP")

        decrypted = tmp_path / "restored.dump"
        subprocess.run(
            [
                openssl_bin,
                "enc",
                "-d",
                "-aes-256-ctr",
                "-pbkdf2",
                "-pass",
                "pass:pg-sec28-secret",
                "-in",
                str(encrypted),
                "-out",
                str(decrypted),
            ],
            check=True,
            capture_output=True,
        )
        assert decrypted.read_bytes().startswith(b"PGDMP")
