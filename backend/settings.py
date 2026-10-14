"""运行时配置：config/config.yaml 持久化，便于目录映射与手工编辑。

- 首次运行自动生成带注释的模板
- 网页「设置」保存后写回 config.yaml（保留注释与格式）
- yaml 中留空/缺失的键回落到环境变量默认值
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from ruamel.yaml import YAML

CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
CONFIG_FILE = CONFIG_DIR / "config.yaml"

# 环境变量默认值（yaml 中对应键留空时生效）
_ENV_DEFAULTS = {
    "emby_url": os.environ.get("EMBY_URL", ""),
    "emby_api_key": os.environ.get("EMBY_API_KEY", ""),
    "emby_library_prefix": os.environ.get("EMBY_LIBRARY_PREFIX", "DY_"),
    "media_subdir": os.environ.get("MEDIA_SUBDIR", ""),
    "host_video_root": os.environ.get("HOST_VIDEO_ROOT", ""),
    "page_size": os.environ.get("PAGE_SIZE", "60"),
    "scan_interval": os.environ.get("SCAN_INTERVAL", "0"),
}

_TEMPLATE = r'''# DY Sort 配置文件
# 网页「设置」保存后也会写回本文件（保留注释）；修改后重启容器生效。
# 注意：yaml 值留空时，将回落使用环境变量（EMBY_URL 等）。

# Emby 服务器地址与 API Key（Emby 控制台 -> 高级 -> API 密钥）
emby_url: ""
emby_api_key: ""

# 只操作名称以该前缀开头的媒体库
emby_library_prefix: "DY_"

# 绑定的短视频子目录：/data 下可能混有其他媒体，填写子目录名（如 "短视频"）
# 作为短视频根目录；留空 = 使用 /data 根。修改后索引会自动重建
media_subdir: ""

# Emby 侧视频根路径：当 Emby 看到的路径与容器内 /data 不一致时填写。
# 例：宿主机 D:\Videos 挂载为容器 /data，则填 host_video_root: 'D:\Videos'
# Windows 反斜杠路径请用单引号包裹，正斜杠路径双引号即可
host_video_root: ""

# 列表每页显示数量
page_size: 60

# 定时重新扫描间隔（分钟）：0 = 关闭；修改后立即生效，无需重启
scan_interval: 0
'''

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.width = 4096

_lock = threading.Lock()
_doc = None        # ruamel CommentedMap（保留注释元数据）
_loaded = False


def _ensure() -> None:
    global _doc, _loaded
    if _loaded:
        return
    with _lock:
        if _loaded:
            return
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():
            try:
                _doc = _yaml.load(CONFIG_FILE.read_text(encoding="utf-8"))
            except Exception:
                _doc = None
            # 解析失败时不覆盖用户文件，回落默认值
            if not isinstance(_doc, dict):
                _doc = {}
        else:
            CONFIG_FILE.write_text(_TEMPLATE, encoding="utf-8")
            _doc = _yaml.load(CONFIG_FILE.read_text(encoding="utf-8"))
        # 留空的键回落环境变量默认值
        for k, v in _ENV_DEFAULTS.items():
            if _doc.get(k) in (None, ""):
                _doc[k] = v
        _loaded = True


def get(key: str, default: str = "") -> str:
    _ensure()
    v = _doc.get(key, default)
    return "" if v is None else str(v)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(float(get(key) or default))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(get(key) or default)
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = True) -> bool:
    v = get(key)
    if v == "":
        return default
    return v.lower() not in ("0", "false", "off", "no")


def set_many(values: dict) -> None:
    """更新配置并写回 config.yaml（ruamel 保留注释）。"""
    _ensure()
    with _lock:
        changed = False
        for k, v in values.items():
            if k not in _ENV_DEFAULTS or v == "******":  # 掩码值不落盘
                continue
            if _doc.get(k) != v:
                _doc[k] = v
                changed = True
        if changed:
            with CONFIG_FILE.open("w", encoding="utf-8") as f:
                _yaml.dump(_doc, f)


def all_settings() -> dict:
    _ensure()
    out = {k: ("" if v is None else str(v)) for k, v in _doc.items()}
    # api_key 只回传掩码，避免泄露
    out["emby_api_key"] = "******" if _doc.get("emby_api_key") else ""
    return out
