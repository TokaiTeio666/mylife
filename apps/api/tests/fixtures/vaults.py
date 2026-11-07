"""确定性 Vault 语料生成（FX-01/02/03/04/10/12）。

所有日期、文本固定；不含真实人名/邮箱/密钥。生成器只返回 `{path: content}`，
由测试上传 ZIP 走真实导入管线。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

DAILY_PERIOD = date(2026, 10, 27)


def _note(path: str, title: str, body: str, *, day: date | None = None) -> str:
    frontmatter = f"---\ndate: {day.isoformat()}\n---\n" if day else ""
    return f"{frontmatter}# {title}\n\n{body}\n"


def fx_daily() -> dict[str, Any]:
    """FX-01：120 篇笔记 + 三日 Todo + 10-27 日记 + 3 篇学习笔记变更 + 错误/方法论。"""
    files: dict[str, str] = {}
    for index in range(120):
        files[f"notes/bulk-{index:03d}.md"] = _note(
            f"notes/bulk-{index:03d}.md",
            f"批量笔记 {index}",
            "缓存与检索的通用记录。",
            day=DAILY_PERIOD - timedelta(days=index % 5),
        )

    # 三日 Todo：写周报 10-25 open、10-26 open、10-27 done；读论文 10-25 open
    files["日记/2026/10.25.md"] = _note(
        "日记/2026/10.25.md",
        "10-25",
        "- [ ] 写周报\n- [ ] 读论文",
        day=date(2026, 10, 25),
    )
    files["日记/2026/10.26.md"] = _note(
        "日记/2026/10.26.md",
        "10-26",
        "- [ ] 写周报",
        day=date(2026, 10, 26),
    )
    files["日记/2026/10.27.md"] = _note(
        "日记/2026/10.27.md",
        "10-27",
        "- [x] 写周报\n\n今天复盘了缓存穿透问题，决定先补布隆过滤器。",
        day=DAILY_PERIOD,
    )

    # 3 篇当天学习笔记变更
    for topic in ("布隆过滤器", "缓存一致性", "限流算法"):
        files[f"学习/{topic}.md"] = _note(
            f"学习/{topic}.md",
            topic,
            f"学习 {topic} 的定义、适用场景与实现要点。",
            day=DAILY_PERIOD,
        )

    # 错误与方法论素材（供抽取；Phase 3 不自动抽取，断言走哨兵）
    files["学习/复盘素材.md"] = _note(
        "学习/复盘素材.md",
        "复盘素材",
        "## 做错的事\n\n- 事件：漏备份\n- 根本原因：忘记设置提醒\n- 代价：1 小时\n"
        "- 正确做法：加入周五检查清单\n- 触发条件：周五下午\n\n"
        "## 正确方法论\n\n- 方法名：先写验证再写实现\n- 来源：实践\n"
        "- 适用场景：接口改造\n- 步骤：写测试、改实现、回归\n- 边界：探索性原型不适用\n"
        "- 落地动作：每个 PR 附测试\n- 验证标准：CI 全绿",
        day=DAILY_PERIOD,
    )
    return files


def fx_weekly() -> dict[str, Any]:
    """FX-02：W43 的 7 份日报 + 台账。"""
    files: dict[str, str] = {}
    start = date(2026, 10, 19)  # 2026-W43 周一
    for offset in range(7):
        day = start + timedelta(days=offset)
        checked = "- [x] 写周报" if offset % 2 == 0 else "- [ ] 写周报"
        files[f"日记/2026/{day.month}.{day.day}.md"] = _note(
            f"日记/2026/{day.month}.{day.day}.md",
            day.isoformat(),
            f"{checked}\n\n当日记录。",
            day=day,
        )
    files["notes/weekly.md"] = _note(
        "notes/weekly.md", "周记", "缓存与检索的周度总结。", day=date(2026, 10, 19)
    )
    return files


def fx_yearly() -> dict[str, Any]:
    """FX-03：全年每周一份日报（含 3 周缺失）+ 台账。"""
    files: dict[str, str] = {}
    day = date(2026, 1, 5)
    week = 0
    while day.year == 2026:
        week += 1
        if week not in {10, 24, 38}:  # 3 周缺失
            files[f"日记/2026/{day.month}.{day.day}.md"] = _note(
                f"日记/2026/{day.month}.{day.day}.md",
                day.isoformat(),
                "- [ ] 复盘\n\n年度记录。",
                day=day,
            )
        day += timedelta(days=7)
    files["notes/yearly.md"] = _note(
        "notes/yearly.md", "年度素材", "全年学习与错误记录。", day=date(2026, 1, 5)
    )
    return files


def fx_empty_day() -> dict[str, Any]:
    """FX-04：当天无 Todo、无日记、无笔记变更（只有历史素材）。"""
    return {
        "notes/history.md": _note(
            "notes/history.md", "历史笔记", "与目标日无关的内容。", day=date(2026, 9, 1)
        )
    }


def fx_cost_budget() -> dict[str, Any]:
    """FX-10：提供足够素材，使 daily 走到预算检查点。"""
    return fx_daily()


def fx_concurrent_runs() -> dict[str, Any]:
    """FX-12：并发触发用的最小素材。"""
    return {
        "日记/2026/10.27.md": _note(
            "日记/2026/10.27.md", "10-27", "- [ ] 并发测试", day=DAILY_PERIOD
        )
    }
