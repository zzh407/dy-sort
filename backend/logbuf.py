"""内存日志环形缓冲：/api/logs 供前端实时查看。

根 logger 挂一个 Handler，所有 dy-sort.* 日志（扫描/Emby 操作
与异常）都进入缓冲；uvicorn 自身日志默认不向上传播，不会混入。
"""
from __future__ import annotations

import logging
import threading
from collections import deque

_MAX_LINES = 800
_buf: deque = deque(maxlen=_MAX_LINES)
_seq = 0
_lock = threading.Lock()

# 不进入页面日志的来源：HTTP 客户端请求日志噪音（如
# "HTTP Request: POST http://... 200 OK"）与 uvicorn 访问日志
_SKIP_PREFIXES = ("httpx", "httpcore", "urllib3", "uvicorn.access")


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(_SKIP_PREFIXES):
            return
        global _seq
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        with _lock:
            _seq += 1
            _log = {"seq": _seq,
                    "ts": round(record.created, 3),
                    "level": record.levelname,
                    "logger": record.name,
                    "msg": msg}
            _buf.append(_log)


def install() -> None:
    """把环形缓冲 Handler 挂到根 logger（幂等）。"""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, RingHandler):
            return
    root.addHandler(RingHandler())


def fetch(after: int = 0) -> dict:
    """返回 seq > after 的日志行与当前最大 seq（前端增量轮询）。"""
    with _lock:
        logs = [r for r in _buf if r["seq"] > after]
        return {"seq": _seq, "logs": logs}
