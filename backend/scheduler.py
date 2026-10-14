"""定时扫描调度：按 scan_interval（分钟）自动触发后台增量扫描。

- 每 CHECK_INTERVAL 秒检查一次配置，设置页保存后立即生效，无需重启
- 上次扫描时间取 scanner.state 的 started_at/finished_at；从未扫描过则以启动时刻为准
  （启动本身已触发一次扫描，避免刚启动就重复扫描）
"""
from __future__ import annotations

import logging
import threading
import time

from . import scanner, settings

log = logging.getLogger("dy-sort.scheduler")

CHECK_INTERVAL = 30      # 检查周期（秒）
_started_at = time.time()

status = {"running": False, "last_trigger": 0.0}


def interval_min() -> int:
    """配置的扫描间隔（分钟），<=0 表示关闭。"""
    try:
        return max(0, int(float(settings.get("scan_interval") or 0)))
    except (TypeError, ValueError):
        return 0


def _last_scan_at() -> float:
    return max(scanner.state["finished_at"], scanner.state["started_at"],
               _started_at)


def next_run_at() -> float:
    """下次自动扫描的时间戳；未启用定时时返回 0。"""
    mins = interval_min()
    return _last_scan_at() + mins * 60 if mins else 0.0


def status_info() -> dict:
    mins = interval_min()
    nxt = next_run_at()
    return {"enabled": mins > 0, "interval_min": mins,
            "next_run_at": nxt,
            "next_in_sec": max(0, round(nxt - time.time())) if nxt else 0,
            "last_trigger": status["last_trigger"],
            "scheduler_running": status["running"]}


def _loop() -> None:
    while True:
        try:
            mins = interval_min()
            if mins > 0 and not scanner.state["running"]:
                waited = time.time() - _last_scan_at()
                if waited >= mins * 60:
                    log.info("定时扫描触发（间隔 %d 分钟，已过 %.0f 分钟）",
                             mins, waited / 60)
                    status["last_trigger"] = time.time()
                    if not scanner.start_background_scan():
                        status["last_trigger"] = 0.0
        except Exception:
            log.exception("定时扫描调度异常")
        time.sleep(CHECK_INTERVAL)


def start() -> bool:
    """启动调度线程（幂等）。"""
    if status["running"]:
        return False
    threading.Thread(target=_loop, daemon=True).start()
    status["running"] = True
    log.info("定时扫描调度已启动：每 %d 秒检查一次（间隔 %d 分钟）",
             CHECK_INTERVAL, interval_min())
    return True