"""本地开发入口（Windows 友好）。

uvicorn 默认使用 ProactorEventLoop，而 psycopg 异步模式需要 SelectorEventLoop。
生产运行在 Linux，不受影响；本机开发请用：
  uv run python -m mylife_api.devserver
"""

from __future__ import annotations

import asyncio
import sys

import uvicorn


def main() -> None:
    config = uvicorn.Config(
        "mylife_api.main:app",
        host="127.0.0.1",
        port=8000,
        log_level="info",
    )
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
