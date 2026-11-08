"""扫描前端硬编码中文（PHASE5-SPEC §10）。

规则：`apps/web/src` 下的 TSX/TS 中，文案必须进 `lib/copy/`。
白名单：注释、`lib/copy/` 自身、语言名称选项（简体中文）、带 `i18n-ignore` 标记的行（数据路径约定）。
命中则退出码 1。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "web" / "src"
CJK = re.compile(r"[一-鿿]")
SKIP_DIRS = {"copy"}
ALLOWED_LITERALS = {"简体中文"}


def strip_comments(line: str, in_block: bool) -> tuple[str, bool]:
    out = []
    i = 0
    while i < len(line):
        if in_block:
            end = line.find("*/", i)
            if end < 0:
                return "".join(out), True
            i, in_block = end + 2, False
            continue
        if line.startswith("/*", i):
            in_block = True
            i += 2
            continue
        if line.startswith("//", i) and (i == 0 or line[i - 1] != ":"):
            break
        out.append(line[i])
        i += 1
    return "".join(out), in_block


def scan(path: Path) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    in_block = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line, in_block = strip_comments(raw, in_block)
        if "i18n-ignore" in raw or not CJK.search(line):
            continue
        if any(token in line for token in ALLOWED_LITERALS):
            continue
        hits.append((number, raw.strip()))
    return hits


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    failures = 0
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in {".ts", ".tsx"} or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        for number, text in scan(path):
            failures += 1
            print(f"{path.relative_to(ROOT.parents[2])}:{number}: {text}")
    if failures:
        print(f"\n{failures} hard-coded CJK string(s) found", file=sys.stderr)
        return 1
    print("check_i18n: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
