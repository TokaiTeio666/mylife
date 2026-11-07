"""依赖方向检查（DIRECTORY-CONSTRAINTS.md §2.1）。

规则（禁止反向依赖）：
  apps/web         -> packages/contracts 之外不得 import 后端代码（TypeScript 无此导入面，检查路径引用）
  apps/api         -> packages/*
  packages/agent   -> contracts, knowledge-runtime
  packages/delivery-> contracts, knowledge-runtime
  packages/retrieval -> contracts
  packages/contracts -> 无（只依赖 pydantic）

Phase 0 仅存在 apps/api 与 packages/contracts；脚本对不存在的包自动跳过，
后续阶段新增包时无需改动。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWED: dict[str, set[str]] = {
    "apps/api": {"mylife_contract", "mylife_agent", "mylife_retrieval", "mylife_runtime", "mylife_delivery"},
    "apps/worker": {"mylife_agent", "mylife_retrieval", "mylife_runtime", "mylife_delivery", "mylife_contract"},
    "apps/dispatcher": {"mylife_contract"},
    "packages/contracts": set(),
    "packages/agent": {"mylife_contract", "mylife_runtime"},
    "packages/knowledge-runtime": {"mylife_contract"},
    "packages/retrieval": {"mylife_contract"},
    "packages/delivery": {"mylife_contract", "mylife_runtime"},
}

# 每个包自身的顶层模块名（自引用不算跨包依赖）。
PACKAGE_MODULES: dict[str, str] = {
    "apps/api": "mylife_api",
    "apps/worker": "mylife_worker",
    "apps/dispatcher": "mylife_dispatcher",
    "packages/contracts": "mylife_contract",
    "packages/agent": "mylife_agent",
    "packages/knowledge-runtime": "mylife_runtime",
    "packages/retrieval": "mylife_retrieval",
    "packages/delivery": "mylife_delivery",
}

KNOWN = set().union(*ALLOWED.values()) | set(PACKAGE_MODULES.values())


def imports_of(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def main() -> int:
    violations: list[str] = []
    for package, declared in ALLOWED.items():
        allowed = set(declared)
        allowed.add(PACKAGE_MODULES[package])
        base = ROOT / package
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if any(part in {"tests", "migrations"} for part in py.parts):
                continue
            for name in imports_of(py) & KNOWN:
                if name not in allowed:
                    violations.append(
                        f"{py.relative_to(ROOT)}: forbidden import {name}"
                    )

    # packages/contracts 不得依赖 app 或运行时包（已由 allowed=∅ 覆盖）；额外禁止 IO。
    contracts = ROOT / "packages/contracts/mylife_contract"
    if contracts.exists():
        for py in contracts.rglob("*.py"):
            names = imports_of(py)
            for forbidden in {"sqlalchemy", "fastapi", "redis", "psycopg", "httpx"}:
                if forbidden in names:
                    violations.append(
                        f"{py.relative_to(ROOT)}: contracts must not depend on {forbidden}"
                    )

    # web 不得 import 后端包（如 @mylife/* 服务端路径）
    web_src = ROOT / "apps/web/src"
    if web_src.exists():
        for ts in list(web_src.rglob("*.ts")) + list(web_src.rglob("*.tsx")):
            text = ts.read_text(encoding="utf-8")
            for forbidden in ("mylife_api", "apps/api", "apps/worker"):
                if forbidden in text:
                    violations.append(
                        f"{ts.relative_to(ROOT)}: web must not reference {forbidden}"
                    )

    if violations:
        print("Dependency direction violations:")
        for v in violations:
            print("  -", v)
        return 1
    print("Dependency direction check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
