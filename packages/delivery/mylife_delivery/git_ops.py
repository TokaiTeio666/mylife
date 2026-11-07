"""Git 交付操作（ARCHITECTURE §8；DIRECTORY-CONSTRAINTS §4）。

安全约定：
- argv 数组调用，不经过 shell；禁用 hooks / submodule / LFS / filter；
- 永不 force push（只允许普通 `git push`）；
- 只写 `reviews_root` 白名单内的相对路径；
- 凭据只通过环境变量/临时 SSH key 传入，不落日志。
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

logger = logging.getLogger("mylife.delivery.git")

MAX_PUSH_RETRIES = 2


class GitDeliveryError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail or code)


class PathRejected(GitDeliveryError):
    def __init__(self, path: str) -> None:
        super().__init__("PATH_REJECTED", path)


def validate_relative_path(path: str) -> str:
    """校验生成物相对路径（DIRECTORY-CONSTRAINTS §4.1）。"""
    if not path or path.startswith("/") or "\\" in path or "//" in path:
        raise PathRejected(path)
    pure = PurePosixPath(path)
    if pure.is_absolute():
        raise PathRejected(path)
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} or part.startswith(".") for part in parts):
        raise PathRejected(path)
    if len(parts) > 8:
        raise PathRejected(path)
    return path


def _common_args(*, allow_local: bool) -> list[str]:
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
        "-c",
        "user.name=mylife",
        "-c",
        "user.email=mylife@localhost",
    ]


def _is_local_remote(remote: str) -> bool:
    if remote.startswith("file://"):
        return True
    parsed = urlparse(remote)
    return parsed.scheme in {"", "file"} or (len(remote) > 2 and remote[1] == ":")


@dataclass
class GitRepo:
    root: Path
    allow_local: bool = False
    timeout_seconds: int = 180
    pat: str | None = None
    private_key: str | None = None
    _tmp_key: Path | None = field(default=None, init=False, repr=False)

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_LFS_SKIP_SMUDGE": "1",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_ASKPASS": os.devnull,
                "GIT_AUTHOR_NAME": "mylife",
                "GIT_AUTHOR_EMAIL": "mylife@localhost",
                "GIT_COMMITTER_NAME": "mylife",
                "GIT_COMMITTER_EMAIL": "mylife@localhost",
            }
        )
        if self.pat:
            token = base64.b64encode(f"x-access-token:{self.pat}".encode()).decode()
            env["GIT_CONFIG_COUNT"] = "1"
            env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
            env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {token}"
        if self.private_key:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".key") as handle:
                handle.write(self.private_key)
                handle.chmod(0o600)
                key_path = Path(handle.name)
            self._tmp_key = key_path
            env["GIT_SSH_COMMAND"] = (
                f"ssh -i {key_path} -o IdentitiesOnly=yes "
                "-o StrictHostKeyChecking=accept-new"
            )
        return env

    def _run(
        self,
        args: list[str],
        *,
        check: bool = True,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = ["git", *_common_args(allow_local=self.allow_local), *args]
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or self.root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._env(),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitDeliveryError("DELIVERY_GIT_UNAVAILABLE", "git 操作超时") from exc
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            tail = detail[-1][:300] if detail else "无 stderr"
            logger.warning("git %s failed rc=%s: %s", args[0], completed.returncode, tail)
            raise GitDeliveryError("DELIVERY_GIT_UNAVAILABLE", tail)
        return completed

    # ---------------------------------------------------------------- 克隆
    def clone(self, remote: str, base_branch: str) -> None:
        if _is_local_remote(remote) and not self.allow_local:
            raise GitDeliveryError("SSRF_BLOCKED", "拒绝本地仓库")
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.parent.mkdir(parents=True, exist_ok=True)
        depth = [] if _is_local_remote(remote) else ["--depth", "1"]
        self._run(
            [
                "clone",
                *depth,
                "--branch",
                base_branch,
                "--single-branch",
                "--no-tags",
                "--no-recurse-submodules",
                "--quiet",
                remote,
                str(self.root),
            ],
            cwd=self.root.parent,
        )

    # ---------------------------------------------------------------- 分支
    def remote_branch_exists(self, branch: str) -> bool:
        completed = self._run(
            ["ls-remote", "--heads", "origin", branch], check=False
        )
        return completed.returncode == 0 and bool(completed.stdout.strip())

    def ensure_branch(self, branch: str, base_branch: str) -> None:
        if self.remote_branch_exists(branch):
            self._run(["fetch", "--quiet", "origin", f"{branch}:refs/remotes/origin/{branch}"])
            self._run(["checkout", "--quiet", "-B", branch, f"origin/{branch}"])
        else:
            self._run(["checkout", "--quiet", "-B", branch, f"origin/{base_branch}"])

    # ---------------------------------------------------------------- 文件
    def read_file(self, path: str) -> str | None:
        validate_relative_path(path)
        target = (self.root / path).resolve()
        if not str(target).startswith(str(self.root.resolve())):
            raise PathRejected(path)
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def write_files(self, files: dict[str, str]) -> None:
        for path, content in files.items():
            validate_relative_path(path)
            target = (self.root / path).resolve()
            if not str(target).startswith(str(self.root.resolve())):
                raise PathRejected(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)

    # ---------------------------------------------------------------- 提交
    def commit_all(self, message: str) -> str | None:
        """提交所有变更；无变更返回 None。"""
        self._run(["add", "-A"])
        staged = self._run(["diff", "--cached", "--quiet"], check=False)
        if staged.returncode == 0:
            return None
        self._run(["commit", "--quiet", "-m", message])
        return self.head_sha()

    def head_sha(self) -> str:
        return self._run(["rev-parse", "HEAD"]).stdout.strip()

    def push(self, branch: str, base_branch: str) -> None:
        """普通 push；非快进时 rebase 重试，绝不 force。"""
        for attempt in range(MAX_PUSH_RETRIES + 1):
            completed = self._run(["push", "--quiet", "origin", branch], check=False)
            if completed.returncode == 0:
                return
            stderr = (completed.stderr or "").lower()
            if "non-fast-forward" not in stderr and "rejected" not in stderr:
                raise GitDeliveryError(
                    "DELIVERY_PR_CONFLICT", (completed.stderr or "").strip()[-300:]
                )
            if attempt >= MAX_PUSH_RETRIES:
                break
            self._run(["fetch", "--quiet", "origin", f"{branch}:refs/remotes/origin/{branch}"], check=False)
            rebase = self._run(["rebase", f"origin/{branch}"], check=False)
            if rebase.returncode != 0:
                self._run(["rebase", "--abort"], check=False)
                raise GitDeliveryError("DELIVERY_PR_CONFLICT", "rebase 失败")
        raise GitDeliveryError("DELIVERY_PR_CONFLICT", "非快进且重试耗尽")

    def log_subject(self, ref: str = "HEAD") -> str:
        return self._run(["log", "-1", "--pretty=%s", ref]).stdout.strip()

    def cleanup(self) -> None:
        if self._tmp_key is not None:
            try:
                self._tmp_key.unlink(missing_ok=True)
            except OSError:
                pass
            self._tmp_key = None


__all__ = [
    "GitDeliveryError",
    "GitRepo",
    "PathRejected",
    "validate_relative_path",
]
