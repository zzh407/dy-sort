"""删除能力（可选启用）：删除单个视频 / 整个博主目录。

边界约束（每条都强制校验，任一不满足即拒绝）：
1. 功能开关：settings.enable_delete=true 才可用（默认关闭）。
2. 隔离环境：视频根目录必须位于容器内挂载点 /data 之下，且不为 /data 根本身。
3. 深度边界：
   - 单视频目录 = 博主目录下的一级子目录（如 /data/博主/视频名）
   - 博主目录   = 短视频根目录下的一级目录（如 /data/博主）
   超出这两层的任何路径一律拒绝，不存在"向上删除"。
4. 名称黑名单：博主/视频名命中 PROTECTED_NAMES 或以 . 开头 → 拒绝。
5. 二次确认：请求必须携带 confirm=true（前端弹窗输入博主名/视频名后置位）。

文件删除顺序：先删 DB 记录（视频→博主），再删磁盘；DB 已删而磁盘残留时
下次扫描不会复现记录，磁盘失败会显式报错给前端。
"""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from . import db, paths, settings
from .config import VIDEO_ROOT

log = logging.getLogger("dy-sort.cleaner")

#: 绝不允许作为博主/视频目录名删除的保留名
PROTECTED_NAMES = {"", ".", "..", "metadata.json", "info.json", "meta.json",
                   "lost+found", "@eaDir", "#recycle", ".Trash-1000"}
#: 扫描器同样忽略 . 开头目录，这里保持一致
_scan_skip = lambda n: n.startswith(".")

MAX_DELETE_BLOGGERS = 50   # 单次批量删除博主数上限


class DeleteError(Exception):
    """删除被拒绝或失败，msg 面向用户。"""


class DeleteDisabled(DeleteError):
    """功能未启用。"""


# ---------- 边界校验（全部通过才允许动手） ----------

def _ensure_enabled() -> None:
    if str(settings.get("enable_delete", "")).lower() not in ("1", "true", "yes", "on"):
        raise DeleteDisabled("删除功能未启用：请在「设置」页开启 enable_delete")


def _check_boundary(p: Path, *, depth: int) -> None:
    """depth=1 博主目录（媒体根下一级），depth=2 视频目录（博主下级）。
    外层以容器挂载点 /data（VIDEO_ROOT）兜底，层内以媒体根（含绑定子目录）判定。"""
    root = VIDEO_ROOT.resolve()
    if not str(p).startswith(str(root) + "/"):
        raise DeleteError("拒绝：目标不在 /data 挂载范围内")
    if p == root:
        raise DeleteError("拒绝：不允许删除 /data 根目录")
    media = paths.media_root().resolve()
    if depth == 1 and p.parent != media:
        raise DeleteError("拒绝：博主目录必须是短视频根目录的一级子目录")
    if depth == 2 and p.parent.parent != media:
        raise DeleteError("拒绝：视频目录必须是博主目录的一级子目录")
    name = p.name
    if name in PROTECTED_NAMES or _scan_skip(name):
        raise DeleteError(f"拒绝：受保护名称 {name!r}")


def _resolve_blogger_dir(blogger: str) -> Path:
    """把博主名解析到磁盘目录并做边界校验；博主必须存在于索引。"""
    b = db.query_one("SELECT id FROM bloggers WHERE dir_name=?", (blogger,))
    if b is None:
        raise DeleteError(f"索引中不存在博主: {blogger}")
    root = paths.media_root().resolve()
    p = (root / blogger).resolve()
    if root not in p.parents:
        raise DeleteError(f"拒绝：博主 {blogger!r} 越出媒体根")
    _check_boundary(p, depth=1)
    return p


def _resolve_video_dir(vid: int) -> tuple[Path, dict]:
    """把视频 id 解析到其视频目录（博主/视频名）并做边界校验。"""
    row = db.query_one(
        "SELECT v.*, b.dir_name AS blogger FROM videos v "
        "JOIN bloggers b ON b.id = v.blogger_id WHERE v.id=?", (vid,))
    if row is None:
        raise DeleteError(f"索引中不存在视频 id={vid}")
    root = paths.media_root().resolve()
    rel = Path(row["rel"])          # 博主/视频名/文件名 或 博主/文件名(旧结构)
    parts = rel.parts
    if len(parts) < 2:
        raise DeleteError(f"拒绝：异常路径 {row['rel']!r}")
    video_dir = (root / parts[0] / parts[1]).resolve()
    if root not in video_dir.parents:
        raise DeleteError("拒绝：视频路径越出媒体根")
    _check_boundary(video_dir, depth=2)
    return video_dir, {"blogger": row["blogger"], "name": row["name"],
                       "rel": row["rel"], "size": row["size"]}


# ---------- 文件系统操作 ----------

def _rmtree_safe(p: Path) -> int:
    """删除目录树，返回字节数。先删可删文件再删目录；失败抛 DeleteError。"""
    total = 0
    try:
        for f in p.rglob("*"):
            if f.is_file() and not f.is_symlink():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        shutil.rmtree(p)
    except OSError as e:
        raise DeleteError(f"磁盘删除失败 {p.name}: {e}") from e
    return total


# ---------- 对外接口 ----------

def delete_video(vid: int, confirm: bool = False) -> dict:
    """删除单个视频：视频目录（含海报/元数据）+ DB 记录。
    旧结构（视频文件直接在博主目录下）只删视频文件本身。"""
    _ensure_enabled()
    if not confirm:
        raise DeleteError("缺少二次确认 (confirm=true)")
    video_dir, info = _resolve_video_dir(vid)
    root = paths.media_root().resolve()

    files_bytes = 0
    if video_dir.is_dir():
        files_bytes = _rmtree_safe(video_dir)
    else:
        # 旧结构：rel = 博主/文件名，文件直接躺在博主目录
        f = root / info["rel"]
        if f.is_file():
            try:
                files_bytes = f.stat().st_size
                f.unlink()
            except OSError as e:
                raise DeleteError(f"磁盘删除失败: {e}") from e
        else:
            raise DeleteError(f"磁盘上不存在: {info['rel']}")

    db.execute("DELETE FROM videos WHERE id=?", (vid,))
    db.execute("UPDATE bloggers SET video_count = "
               "(SELECT COUNT(*) FROM videos WHERE videos.blogger_id = bloggers.id) "
               "WHERE dir_name=?", (info["blogger"],))
    log.info("删除视频: %s（%.1f MB）", info["rel"], files_bytes / 1048576)
    return {"deleted": "video", "blogger": info["blogger"],
            "name": info["name"], "rel": info["rel"],
            "freed_bytes": files_bytes}


def delete_blogger(blogger: str, confirm: bool = False,
                   remove_from_emby: bool = True) -> dict:
    """删除整个博主：目录树 + 全部视频记录 + 博主记录 + 可选移出 Emby 库路径。"""
    _ensure_enabled()
    if not confirm:
        raise DeleteError("缺少二次确认 (confirm=true)")
    p = _resolve_blogger_dir(blogger)

    vids = db.query("SELECT COUNT(*) c FROM videos WHERE blogger_id=" 
                    "(SELECT id FROM bloggers WHERE dir_name=?)", (blogger,))
    n_videos = vids[0]["c"] if vids else 0

    files_bytes = _rmtree_safe(p) if p.is_dir() else 0

    # DB：视频随 blogger 外键 CASCADE，但显式删除更直观
    db.execute("DELETE FROM videos WHERE blogger_id="
               "(SELECT id FROM bloggers WHERE dir_name=?)", (blogger,))
    db.execute("DELETE FROM bloggers WHERE dir_name=?", (blogger,))
    db.execute("DELETE FROM emby_map WHERE blogger=?", (blogger,))

    emby_removed = False
    if remove_from_emby:
        try:
            from .emby import EmbyClient, EmbyError
            emby_removed = EmbyClient().remove_blogger_path(blogger)
        except EmbyError as e:
            log.warning("移出 Emby 失败（不影响本地删除）: %s", e)
        except Exception:
            pass

    log.info("删除博主: %s（%d 个视频，%.1f MB，emby_removed=%s）",
             blogger, n_videos, files_bytes / 1048576, emby_removed)
    return {"deleted": "blogger", "blogger": blogger, "videos": n_videos,
            "freed_bytes": files_bytes, "emby_removed": emby_removed}


def delete_bloggers(bloggers: list[str], confirm: bool = False,
                    remove_from_emby: bool = True) -> dict:
    """批量删除多个博主（逐个执行，失败项跳过并报告）。"""
    if not isinstance(bloggers, list) or not (1 <= len(bloggers) <= MAX_DELETE_BLOGGERS):
        raise DeleteError(f"bloggers 需为 1~{MAX_DELETE_BLOGGERS} 个的列表")
    out = {"deleted": [], "failed": []}
    for b in bloggers:
        try:
            out["deleted"].append(delete_blogger(b, confirm=confirm,
                                                 remove_from_emby=remove_from_emby))
        except DeleteError as e:
            out["failed"].append({"blogger": b, "error": str(e)})
    return out


def preview_blogger(blogger: str) -> dict:
    """删除前预览：统计将删除的视频数/字节/磁盘状态/Emby 状态。"""
    _ensure_enabled()
    p = _resolve_blogger_dir(blogger)
    row = db.query_one(
        "SELECT b.video_count, b.in_emby, COUNT(v.id) c, "
        "COALESCE(SUM(v.size),0) sz FROM bloggers b "
        "LEFT JOIN videos v ON v.blogger_id = b.id WHERE b.dir_name=?",
        (blogger,))
    return {"blogger": blogger,
            "videos": row["c"] or 0,
            "bytes": row["sz"] or 0,
            "in_emby": bool(row["in_emby"]),
            "on_disk": p.is_dir(),
            "path": str(p)}


def preview_video(vid: int) -> dict:
    """删除前预览（单视频）。"""
    _ensure_enabled()
    _, info = _resolve_video_dir(vid)
    root = paths.media_root().resolve()
    target = root / Path(info["rel"]).parts[0] / Path(info["rel"]).parts[1]
    return {**info, "on_disk": target.is_dir(), "path": str(target)}
