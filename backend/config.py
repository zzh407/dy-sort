"""静态配置：目录结构与扩展名约定。运行时配置见 settings.py。"""
import os

from pathlib import Path

# 视频根目录（容器内挂载路径）
VIDEO_ROOT = Path(os.environ.get("VIDEO_ROOT", "/data"))

# 元数据文件候选名（按优先级，放在博主目录或视频目录均可）
METADATA_CANDIDATES = ["metadata.json", "info.json", "meta.json"]
# 海报候选名（不含扩展名部分）
POSTER_CANDIDATES = ["poster", "cover", "thumb", "fanart"]

# 视频扩展名
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv"}
# 图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
