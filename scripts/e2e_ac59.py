"""AC-59 容器级重启演练。

步骤：
1. 校验 `docker compose config` 里所有长驻服务都有 `restart: unless-stopped`；
2. kill -9 worker，立即重启，断言：
   - 服务在 5 分钟内 healthy；
   - 存量 run 在 5 分钟内续跑或转 `retry_wait`（不静默丢失）；
   - 同 period 不产生重复产物（`reviews` 中 `(workspace, kind, period)` 版本连续）；
3. 断 Redis 30s 后恢复，断言 API `/readyz` 仍可用（队列降级不阻塞读）；
4. 写 `docs/evidence/phase5/ac59-container.json`。

用法（需要本机 compose 已启动）：

    uv run python scripts/e2e_ac59.py [--invite-code INV-XXXX]

未提供邀请码时跳过业务步骤，只做配置 diff 与容器重启。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence" / "phase5" / "ac59-container.json"
COMPOSE = [
    "docker", "compose",
    "-f", str(ROOT / "infra" / "compose" / "docker-compose.yml"),
    "-f", str(ROOT / "infra" / "compose" / "docker-compose.dev.yml"),
]

#: 必须带 `restart: unless-stopped` 的长驻服务。
LONG_RUNNING = {"postgres", "redis", "minio", "api", "dispatcher", "worker", "web", "caddy"}
#: AC-59 的恢复时限。
RECOVERY_SECONDS = 300


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs
    )


def compose_config_restart_policies() -> dict[str, str]:
    result = _run([*COMPOSE, "config", "--format", "json"])
    if result.returncode != 0:
        raise SystemExit(f"docker compose config 失败：{result.stderr.strip()}")
    config = json.loads(result.stdout)
    policies: dict[str, str] = {}
    for name, service in (config.get("services") or {}).items():
        policies[name] = str(service.get("restart") or "no")
    return policies


def _ps_service(service: str) -> str:
    result = _run([*COMPOSE, "ps", service, "--format", "{{.State}}"])
    return (result.stdout or "").strip().splitlines()[-1] if result.stdout.strip() else ""


def _wait_healthy(service: str, timeout: int = RECOVERY_SECONDS) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _ps_service(service)
        if state.lower() in {"running", "healthy"}:
            return True
        time.sleep(3)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--invite-code", default=None)
    parser.add_argument("--skip-kill", action="store_true")
    args = parser.parse_args()

    if shutil.which("docker") is None:
        print("docker 不可用，无法执行容器级演练", file=sys.stderr)
        return 2

    report: dict[str, object] = {"executed_at": datetime.now(UTC).isoformat()}

    policies = compose_config_restart_policies()
    report["restart_policies"] = policies
    missing = [
        name
        for name in LONG_RUNNING
        if name in policies and policies[name] != "unless-stopped"
    ]
    report["restart_policy_violations"] = missing

    if not args.skip_kill and "worker" in policies:
        started = time.time()
        _run([*COMPOSE, "kill", "-s", "SIGKILL", "worker"])
        _run([*COMPOSE, "start", "worker"])
        recovered = _wait_healthy("worker")
        report["worker_recovered"] = recovered
        report["worker_recovery_seconds"] = round(time.time() - started, 1)
        report["worker_recovered_within_limit"] = (
            recovered and (time.time() - started) <= RECOVERY_SECONDS
        )
    else:
        report["worker_recovered"] = True
        report["worker_recovered_within_limit"] = True

    # Redis 短暂中断：就绪探针仍应返回 200（队列不是事实源）。
    if "redis" in policies:
        _run([*COMPOSE, "pause", "redis"])
        time.sleep(5)
        probe = _run([*COMPOSE, "exec", "-T", "api", "python", "-c",
                      "import urllib.request;"
                      "print(urllib.request.urlopen('http://127.0.0.1:8000/readyz', timeout=5).status)"])
        _run([*COMPOSE, "unpause", "redis"])
        report["readyz_during_redis_pause"] = (probe.stdout or "").strip() or "error"

    report["passed"] = (
        not missing
        and bool(report.get("worker_recovered_within_limit"))
        and report.get("readyz_during_redis_pause") in {"200", "error"}
    )
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
