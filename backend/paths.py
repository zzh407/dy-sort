"""路径解析：/data 挂载点 + 绑定的子目录（media_subdir）。

Emby 内除短视频外可能还有其他媒体，因此允许把 /data 下某个子目录
绑定为短视频根目录；所有扫描与 Emby 路径推导都基于该绑定。"""
from __future__ import annotations

from pathlib import Path

from . import settings
from .config import VIDEO_ROOT


class BindError(Exception):
    pass


def validate_subdir(subdir: str) -> None:
    """校验子目录绑定是否合法（存在且是目录）。空串表示绑定根目录。"""
    if not subdir:
        return
    p = VIDEO_ROOT / subdir
    if not p.is_dir():
        raise BindError(f"目录不存在: /data/{subdir}")


def media_root() -> Path:
    """短视频根目录（绑定子目录或 /data 根）。"""
    sub = settings.get("media_subdir").strip("/\\")
    if not sub:
        return VIDEO_ROOT
    # 防目录穿越
    root = VIDEO_ROOT.resolve()
    p = (VIDEO_ROOT / sub).resolve()
    if p != root and root not in p.parents:
        return VIDEO_ROOT
    return p if p.is_dir() else VIDEO_ROOT


def rel_from_media_root(abs_path: Path) -> str:
    """绝对路径相对于短视频根目录的 POSIX 风格相对路径。"""
    try:
        return abs_path.resolve().relative_to(media_root().resolve()).as_posix()
    except ValueError:
        return abs_path.name
