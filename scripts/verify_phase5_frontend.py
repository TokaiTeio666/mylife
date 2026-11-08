"""Phase 5 前端与合规自动化验收（AC-54 / AC-62 / AC-63 / AC-64）。

使用本机无头 Chromium（Edge/Chrome CDP）与真实运行中的 Caddy/Next.js/API 容器：
1. AC-62：在 1440 / 768 / 390 三种视口宽度下打开公共页与业务页，实测 DOM
   `scrollWidth <= innerWidth`（横向溢出为 0），校验 PWA manifest / sw.js /
   offline.html，并导出截图；
2. AC-63：执行 `check_i18n.py` 扫描硬编码中文（0 违规），切换 `mylife_locale`
   验证 `<html lang>` 与至少 3 个页面的可见文案变化；
3. AC-64：匿名请求 `/privacy` 与 `/terms` 返回 200，校验数据出境供应商说明与
   “已合并 Git 历史保留、不承诺抹除”告知，核对导出数据类别齐全。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
import websockets

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "evidence" / "phase5" / "frontend"
BASE_URL = os.environ.get("MYLIFE_E2E_BASE", "https://localhost")
DB_HOST = os.environ.get("MYLIFE_DB_HOST", "localhost")
DB_PORT = os.environ.get("MYLIFE_DB_PORT", "25432")
DB_NAME = os.environ.get("MYLIFE_DB_NAME", "mylife")
DB_USER = os.environ.get("POSTGRES_USER", "mylife_migrator")
DB_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "change-me-migrator")


def _find_browser() -> str:
    for candidate in (
        shutil.which("msedge"),
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise SystemExit("未找到可用的无头浏览器（Edge/Chrome/Chromium）")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _prepare_admin_session() -> tuple[str, str]:
    """在本地 compose 库准备一个带 platform_admin + owner 身份的登录会话。"""
    email = f"fe-verify-{uuid.uuid4().hex[:8]}@example.com"
    password = "correct-horse-9"
    invite = f"INV-FE{uuid.uuid4().hex[:8].upper()}"

    dsn = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        creator = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        creator_id = creator[0] if creator else None
        conn.execute(
            "INSERT INTO invite_codes (id, code, created_by, expires_at) "
            "VALUES (gen_random_uuid(), %s, %s, now() + interval '1 day')",
            (invite, creator_id),
        )

    with httpx.Client(verify=False, trust_env=False, timeout=30.0) as client:
        client.get(f"{BASE_URL}/api/v1/auth/csrf")
        csrf = client.cookies.get("mylife_csrf")
        assert csrf, "csrf cookie 缺失"
        reg = client.post(
            f"{BASE_URL}/api/v1/auth/register",
            json={
                "email": email,
                "password": password,
                "display_name": "FE 验收员",
                "invite_code": invite,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert reg.status_code == 201, reg.text
        session_cookie = client.cookies.get("mylife_session")
        assert session_cookie, "session cookie 缺失"

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE users SET is_platform_admin = true WHERE email = %s", (email,)
        )
    return email, session_cookie


class CdpSession:
    def __init__(self, ws) -> None:
        self._ws = ws
        self._seq = 0

    async def call(self, method: str, params: dict | None = None) -> dict:
        self._seq += 1
        req_id = self._seq
        await self._ws.send(
            json.dumps({"id": req_id, "method": method, "params": params or {}})
        )
        while True:
            raw = await self._ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 失败: {msg['error']}")
                return msg.get("result") or {}

    async def goto(self, url: str, wait_seconds: float = 1.2) -> None:
        await self.call("Page.navigate", {"url": url})
        await asyncio.sleep(wait_seconds)
        for _ in range(20):
            state = await self.eval("document.readyState")
            if state == "complete":
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)

    async def eval(self, expression: str):
        res = await self.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        return (res.get("result") or {}).get("value")

    async def set_viewport(self, width: int, height: int) -> None:
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": width <= 480,
            },
        )

    async def screenshot(self, path: Path) -> None:
        data = await self.call("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(data["data"]))


async def _run_browser_checks(session_token: str) -> tuple[dict, dict]:
    browser = _find_browser()
    port = _free_port()
    profile_dir = tempfile.mkdtemp(prefix="mylife-fe-cdp-")
    proc = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-proxy-server",
            "--ignore-certificate-errors",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                resp = httpx.get(
                    f"http://127.0.0.1:{port}/json/list", trust_env=False, timeout=2.0
                )
                targets = [t for t in resp.json() if t.get("type") == "page"]
                if targets:
                    ws_url = targets[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.3)
        if not ws_url:
            raise RuntimeError("无法连接无头浏览器 CDP 端口")

        async with websockets.connect(ws_url, max_size=32 * 1024 * 1024) as ws:
            cdp = CdpSession(ws)
            await cdp.call("Page.enable")
            await cdp.call("Network.enable")
            await cdp.call("Runtime.enable")

            # 先测量匿名公共页（1440 / 768 / 390）
            widths = [(1440, 900), (768, 1024), (390, 844)]
            matrix: list[dict] = []

            for path in ("/login", "/privacy", "/terms"):
                for w, h in widths:
                    await cdp.set_viewport(w, h)
                    await cdp.goto(f"{BASE_URL}{path}")
                    metrics = await cdp.eval(
                        "({scrollWidth: document.documentElement.scrollWidth,"
                        " innerWidth: window.innerWidth,"
                        " bodyLen: document.body.innerText.trim().length})"
                    )
                    overflow = int(metrics["scrollWidth"]) - int(metrics["innerWidth"])
                    matrix.append(
                        {
                            "path": path,
                            "width": w,
                            "scroll_width": int(metrics["scrollWidth"]),
                            "inner_width": int(metrics["innerWidth"]),
                            "overflow_x": max(0, overflow),
                            "non_blank": int(metrics["bodyLen"]) > 0,
                        }
                    )
                    if path == "/privacy" and w == 390:
                        await cdp.screenshot(OUT_DIR / "mobile-390-privacy.png")

            # 离线壳截图
            await cdp.set_viewport(390, 844)
            await cdp.goto(f"{BASE_URL}/offline.html", wait_seconds=0.6)
            offline_text = await cdp.eval("document.body.innerText.trim()")
            await cdp.screenshot(OUT_DIR / "offline-shell.png")

            # 注入登录会话 Cookie，验证业务页与管理后台（1440 / 768 / 390）
            await cdp.call(
                "Network.setCookie",
                {
                    "name": "mylife_session",
                    "value": session_token,
                    "domain": "localhost",
                    "path": "/",
                    "secure": True,
                    "httpOnly": True,
                    "sameSite": "Lax",
                },
            )

            auth_pages = (
                "/dashboard",
                "/search",
                "/documents",
                "/chat",
                "/settings",
                "/admin/users",
            )
            for path in auth_pages:
                for w, h in widths:
                    await cdp.set_viewport(w, h)
                    await cdp.goto(f"{BASE_URL}{path}", wait_seconds=1.5)
                    metrics = await cdp.eval(
                        "({scrollWidth: document.documentElement.scrollWidth,"
                        " innerWidth: window.innerWidth,"
                        " bodyLen: document.body.innerText.trim().length,"
                        " pathname: location.pathname})"
                    )
                    overflow = int(metrics["scrollWidth"]) - int(metrics["innerWidth"])
                    matrix.append(
                        {
                            "path": path,
                            "rendered_path": metrics["pathname"],
                            "width": w,
                            "scroll_width": int(metrics["scrollWidth"]),
                            "inner_width": int(metrics["innerWidth"]),
                            "overflow_x": max(0, overflow),
                            "non_blank": int(metrics["bodyLen"]) > 0,
                        }
                    )
                    if path == "/settings" and w == 1440:
                        await cdp.screenshot(OUT_DIR / "desktop-1440-settings.png")
                    elif path == "/settings" and w == 390:
                        await cdp.screenshot(OUT_DIR / "mobile-390-settings.png")
                    elif path == "/dashboard" and w == 768:
                        await cdp.screenshot(OUT_DIR / "tablet-768-dashboard.png")
                    elif path == "/admin/users" and w == 1440:
                        await cdp.screenshot(OUT_DIR / "desktop-1440-admin-users.png")

            # AC-63：对比 zh-CN 与 en-US 下 4 个页面的 <html lang> 与可见文本
            await cdp.set_viewport(1440, 900)
            locale_diffs: list[dict] = []
            for path in ("/privacy", "/terms", "/dashboard", "/settings"):
                await cdp.call(
                    "Network.setCookie",
                    {
                        "name": "mylife_locale",
                        "value": "zh-CN",
                        "domain": "localhost",
                        "path": "/",
                    },
                )
                await cdp.goto(f"{BASE_URL}{path}", wait_seconds=1.2)
                zh = await cdp.eval(
                    "({lang: document.documentElement.lang,"
                    " h1: (document.querySelector('h1')||{}).innerText || '',"
                    " text: document.body.innerText.slice(0, 240)})"
                )

                await cdp.call(
                    "Network.setCookie",
                    {
                        "name": "mylife_locale",
                        "value": "en-US",
                        "domain": "localhost",
                        "path": "/",
                    },
                )
                await cdp.goto(f"{BASE_URL}{path}", wait_seconds=1.2)
                en = await cdp.eval(
                    "({lang: document.documentElement.lang,"
                    " h1: (document.querySelector('h1')||{}).innerText || '',"
                    " text: document.body.innerText.slice(0, 240)})"
                )
                locale_diffs.append(
                    {
                        "path": path,
                        "zh_lang": zh["lang"],
                        "en_lang": en["lang"],
                        "zh_heading": zh["h1"],
                        "en_heading": en["h1"],
                        "changed": zh["text"] != en["text"],
                    }
                )

            return (
                {"matrix": matrix, "offline_shell_text": offline_text},
                {"locale_diffs": locale_diffs},
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _, session_cookie = _prepare_admin_session()

    # 1. PWA 静态资源与匿名法务页 HTTP 校验
    with httpx.Client(verify=False, trust_env=False, timeout=20.0) as anon:
        manifest_resp = anon.get(f"{BASE_URL}/manifest.webmanifest")
        assert manifest_resp.status_code == 200, manifest_resp.text
        manifest = manifest_resp.json()

        sw_resp = anon.get(f"{BASE_URL}/sw.js")
        assert sw_resp.status_code == 200
        sw_source = sw_resp.text

        offline_resp = anon.get(f"{BASE_URL}/offline.html")
        assert offline_resp.status_code == 200

        privacy_resp = anon.get(f"{BASE_URL}/privacy")
        terms_resp = anon.get(f"{BASE_URL}/terms")
        assert privacy_resp.status_code == 200
        assert terms_resp.status_code == 200

    # 2. check_i18n 静态扫描
    i18n_scan = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_i18n.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    # 3. 真实浏览器多视口、截图与多语言切换
    ac62_browser, ac63_browser = asyncio.run(_run_browser_checks(session_cookie))

    overflow_violations = [
        row for row in ac62_browser["matrix"] if row["overflow_x"] > 0 or not row["non_blank"]
    ]
    pwa_checks = {
        "manifest_status": manifest_resp.status_code,
        "name": manifest.get("name"),
        "start_url": manifest.get("start_url"),
        "display": manifest.get("display"),
        "icons_count": len(manifest.get("icons") or []),
        "has_maskable_icon": any(
            "maskable" in str(icon.get("purpose", "")) for icon in (manifest.get("icons") or [])
        ),
        "sw_status": sw_resp.status_code,
        "sw_skips_api": 'startsWith("/api/")' in sw_source,
        "offline_status": offline_resp.status_code,
        "offline_non_blank": bool(ac62_browser["offline_shell_text"]),
    }
    ac62_report = {
        "executed_at": datetime.now(UTC).isoformat(),
        "viewports_tested": [1440, 768, 390],
        "responsive_matrix": ac62_browser["matrix"],
        "overflow_violations": overflow_violations,
        "pwa": pwa_checks,
        "screenshots": sorted(p.name for p in OUT_DIR.glob("*.png")),
        "passed": (
            not overflow_violations
            and pwa_checks["display"] == "standalone"
            and pwa_checks["sw_skips_api"]
            and pwa_checks["offline_non_blank"]
        ),
    }
    (OUT_DIR / "ac62-responsive-pwa.json").write_text(
        json.dumps(ac62_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    changed_pages = [row["path"] for row in ac63_browser["locale_diffs"] if row["changed"]]
    ac63_report = {
        "executed_at": datetime.now(UTC).isoformat(),
        "default_locale": "zh-CN",
        "static_scan_exit_code": i18n_scan.returncode,
        "static_scan_output": i18n_scan.stdout.strip(),
        "hardcoded_cjk_violations": 0 if i18n_scan.returncode == 0 else 1,
        "pages_verified": ac63_browser["locale_diffs"],
        "changed_page_count": len(changed_pages),
        "passed": i18n_scan.returncode == 0 and len(changed_pages) >= 3,
    }
    (OUT_DIR / "ac63-i18n.json").write_text(
        json.dumps(ac63_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    export_reconcile = json.loads(
        (ROOT / "docs" / "evidence" / "phase5" / "export-reconciliation.json").read_text(
            encoding="utf-8"
        )
    )
    privacy_html = privacy_resp.text
    terms_html = terms_resp.text
    ac64_report = {
        "executed_at": datetime.now(UTC).isoformat(),
        "privacy": {
            "anonymous_status": privacy_resp.status_code,
            "discloses_cross_border_providers": (
                "DeepSeek" in privacy_html
                and "OpenAI" in privacy_html
                and "BGE-M3" in privacy_html
                and "数据出境" in privacy_html
            ),
            "discloses_git_history_retention": (
                "已合并到 Git 的历史提交" in privacy_html and "不承诺" in privacy_html
            ),
        },
        "terms": {
            "anonymous_status": terms_resp.status_code,
            "discloses_cross_border": "数据出境" in terms_html,
            "discloses_git_history_retention": (
                "已合并到 Git 的历史提交" in terms_html and "不承诺抹除" in terms_html
            ),
        },
        "export_categories": export_reconcile["categories"],
        "export_count_difference": export_reconcile["count_difference"],
        "passed": (
            privacy_resp.status_code == 200
            and terms_resp.status_code == 200
            and "DeepSeek" in privacy_html
            and "不承诺" in privacy_html
            and "不承诺抹除" in terms_html
            and set(export_reconcile["categories"])
            >= {"documents", "reviews", "conversations", "settings", "usage", "audit"}
        ),
    }
    (OUT_DIR / "ac64-legal.json").write_text(
        json.dumps(ac64_report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = {
        "ac62_passed": ac62_report["passed"],
        "ac63_passed": ac63_report["passed"],
        "ac64_passed": ac64_report["passed"],
        "screenshots": ac62_report["screenshots"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if all([ac62_report["passed"], ac63_report["passed"], ac64_report["passed"]]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
