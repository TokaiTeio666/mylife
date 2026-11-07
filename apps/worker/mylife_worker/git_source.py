"""Git 导入源（FR-B1；ARCHITECTURE §10；SEC-12/15）。

安全约束：
- URL 仅 https/ssh（或测试显式开启的本地仓库）；
- 拒绝私网/回环/链路本地/保留地址，拒绝 file://、ext::、带内嵌凭据的 URL；
- clone 禁用 hooks/submodule/LFS/filter，argv 数组调用，不经过 shell；
- 克隆后拒绝符号链接。
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from mylife_runtime.paths import validate_vault_path

from mylife_worker.ignore import IgnoreMatcher, is_image, is_markdown
from mylife_worker.zip_source import ExtractedFile

_SCP_LIKE = re.compile(r"^(?P<user>[^@/\s]+)@(?P<host>[^:/\s]+):(?P<path>.+)$")
_MAX_FILE_BYTES = 20 * 1024 * 1024
logger = logging.getLogger("mylife.worker.git")


class GitRejected(Exception):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        self.message = message
        super().__init__(message)


@dataclass(slots=True)
class GitTarget:
    url: str
    host: str
    scheme: str


@dataclass(slots=True)
class CloneResult:
    commit_sha: str
    author_date: date | None
    files: list[ExtractedFile]


def _is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_global


def validate_git_target(url: str, *, allow_local: bool = False) -> GitTarget:
    if not url or len(url) > 2048:
        raise GitRejected("SSRF_BLOCKED", "URL 为空或过长")
    if url.startswith("file://") or url.startswith("ext::"):
        if not allow_local:
            raise GitRejected("SSRF_BLOCKED", "拒绝 file:// 与 ext::")
        parsed = urlparse(url)
        return GitTarget(url=url, host=parsed.hostname or "localhost", scheme="file")

    scp = _SCP_LIKE.match(url)
    if scp and "://" not in url:
        host = scp.group("host")
        scheme = "ssh"
    else:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        if parsed.username or parsed.password:
            raise GitRejected("SSRF_BLOCKED", "URL 不得内嵌凭据")

    if scheme not in {"https", "ssh", "git+ssh"}:
        raise GitRejected("SSRF_BLOCKED", f"不支持的协议: {scheme}")
    if not host:
        raise GitRejected("SSRF_BLOCKED", "缺少主机名")

    if allow_local and host in {"localhost", "127.0.0.1", "::1"}:
        return GitTarget(url=url, host=host, scheme=scheme)

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise GitRejected("INGEST_REPO_UNREACHABLE", f"无法解析主机: {host}") from exc
    addresses = {info[4][0] for info in infos}
    if not addresses or any(not _is_public_address(addr) for addr in addresses):
        raise GitRejected("SSRF_BLOCKED", "目标地址不是公网地址")
    return GitTarget(url=url, host=host, scheme=scheme)


def _git_env(*, pat: str | None, key_path: Path | None, home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_LFS_SKIP_SMUDGE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ASKPASS": os.devnull,
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home / ".config"),
        }
    )
    if pat:
        token = base64.b64encode(f"x-access-token:{pat}".encode()).decode()
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {token}"
    if key_path is not None:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {key_path} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
        )
    return env


def _git_common_args(*, allow_local: bool = False) -> list[str]:
    return [
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        f"protocol.file.allow={'always' if allow_local else 'never'}",
        "-c",
        "protocol.ext.allow=never",
        "-c",
        "safe.directory=*",
        "-c",
        "core.symlinks=false",
        "-c",
        "core.autocrlf=false",
        "-c",
        "submodule.recurse=false",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.required=false",
    ]


def _reject_symlinks(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            candidate = Path(dirpath) / name
            try:
                mode = os.lstat(candidate).st_mode
            except OSError:
                continue
            if stat.S_ISLNK(mode):
                raise GitRejected("INGEST_AUTH_FAILED", f"仓库包含符号链接: {candidate}")
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise GitRejected("INGEST_AUTH_FAILED", f"仓库包含特殊文件: {candidate}")


def clone_repository(
    url: str,
    branch: str,
    destination: Path,
    *,
    auth_type: str = "pat",
    credential: str | None = None,
    allow_local: bool = False,
    timeout_seconds: int = 600,
) -> CloneResult:
    target = validate_git_target(url, allow_local=allow_local)
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="mylife-git-") as tmp:
        tmp_path = Path(tmp)
        key_path: Path | None = None
        pat: str | None = None

        if credential:
            if auth_type in {"pat", "app"}:
                pat = credential
            elif auth_type == "deploy_key":
                key_path = tmp_path / "id_key"
                key_path.write_text(credential, encoding="utf-8")
                key_path.chmod(0o600)

        env = _git_env(pat=pat, key_path=key_path, home=tmp_path)
        depth_args = [] if target.scheme == "file" else ["--depth", "1"]
        command = [
            "git",
            *_git_common_args(allow_local=allow_local),
            "clone",
            *depth_args,
            "--branch",
            branch,
            "--single-branch",
            "--no-tags",
            "--no-recurse-submodules",
            "--quiet",
            url,
            str(destination),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise GitRejected("INGEST_REPO_UNREACHABLE", "克隆超时") from exc

        if completed.returncode != 0:
            logger.warning(
                "git clone failed rc=%s cwd=%s stderr=%s",
                completed.returncode,
                destination,
                (completed.stderr or "")[:2000],
            )
            shutil.rmtree(destination, ignore_errors=True)
            stderr = (completed.stderr or "").lower()
            if "authentication" in stderr or "permission denied" in stderr:
                raise GitRejected("INGEST_AUTH_FAILED", "Git 认证失败")
            if "remote branch" in stderr and "not found" in stderr:
                raise GitRejected("INGEST_BRANCH_NOT_FOUND", f"分支不存在: {branch}")
            tail = (completed.stderr or "").strip().splitlines()
            detail = tail[-1][:300] if tail else "无 stderr"
            raise GitRejected("INGEST_REPO_UNREACHABLE", f"无法访问 Git 仓库: {detail}")

        _reject_symlinks(destination)

        sha = subprocess.run(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout.strip()
        author = subprocess.run(
            ["git", "-C", str(destination), "log", "-1", "--format=%aI"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        ).stdout.strip()
        try:
            author_date = datetime.fromisoformat(author).date()
        except ValueError:
            author_date = None

    files = collect_files(destination, IgnoreMatcher())
    return CloneResult(commit_sha=sha, author_date=author_date, files=files)


def collect_files(root: Path, matcher: IgnoreMatcher) -> list[ExtractedFile]:
    collected: list[ExtractedFile] = []
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git" and not matcher.is_ignored(d)]
        for name in filenames:
            full = Path(dirpath) / name
            relative = full.relative_to(root).as_posix()
            if matcher.is_ignored(relative):
                continue
            if not (is_markdown(relative) or is_image(relative)):
                continue
            try:
                validate_vault_path(relative)
            except ValueError:
                continue
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_BYTES:
                continue
            mtime = datetime.fromtimestamp(full.stat().st_mtime)
            collected.append(ExtractedFile(path=relative, size=size, mtime=mtime))
    return collected
