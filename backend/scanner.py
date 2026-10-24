"""扫描 /data：结构为 /博主ID/视频名/视频名.mp4，增量入库 SQLite。

- 一级目录 = 博主ID（Emby 以此目录为单位加入媒体库）
- 二级目录 = 视频名（元数据/海报放在这里，与视频文件同级）
- 旧结构 /博主ID/文件名.mp4 仍兼容。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path

from . import db, paths
from .config import (IMAGE_EXTS, METADATA_CANDIDATES, POSTER_CANDIDATES,
                     VIDEO_EXTS)

log = logging.getLogger("dy-sort.scanner")

# 扫描互斥锁：保证同一时刻只有一个扫描线程
_scan_lock = threading.Lock()

# 扫描状态（内存）
state = {"running": False, "phase": "", "done": 0, "total": 0,
         "added": 0, "removed": 0, "started_at": 0.0, "finished_at": 0.0,
         "error": ""}


def md5_key(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()[:16]


def read_metadata(dir_path: Path) -> dict:
    try:
        return _metadata_from_entries(sorted(dir_path.iterdir()))
    except OSError:
        return {}


def _metadata_from_entries(entries: list[Path]) -> dict:
    """从已 iterdir 的条目里找元数据文件（避免重复遍历目录）。"""
    by_name = {p.name: p for p in entries}
    for name in METADATA_CANDIDATES:
        p = by_name.get(name)
        if p is None or not p.is_file():
            continue
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def find_poster(dir_path: Path) -> Path | None:
    try:
        return _poster_from_entries(sorted(dir_path.iterdir()))
    except OSError:
        return None


def _poster_from_entries(entries: list[Path]) -> Path | None:
    """从已 iterdir 的条目里找海报：优先候选名，其次第一张图。"""
    files = [p for p in entries if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    for base in POSTER_CANDIDATES:
        for p in files:
            if p.stem.lower() == base:
                return p
    return files[0] if files else None


def scan_state() -> dict:
    s = dict(state)
    if s["running"]:
        s["elapsed"] = round(time.time() - s["started_at"], 1)
    return s


def run_full_scan(force: bool = False) -> dict:
    """全量增量扫描（幂等，可重复调用）。阻塞式，由后台线程调用。"""
    if state["running"]:
        return {"skipped": True, "reason": "扫描已在进行中"}
    state.update(running=True, phase="扫描目录", done=0, total=0,
                 added=0, removed=0, started_at=time.time(),
                 finished_at=0.0, error="")
    try:
        now = time.time()
        MEDIA_ROOT = paths.media_root()

        if not MEDIA_ROOT.is_dir():
            raise RuntimeError(f"视频目录不存在: {MEDIA_ROOT}")

        blogger_dirs = [d for d in sorted(MEDIA_ROOT.iterdir())
                        if d.is_dir() and not d.name.startswith(".")]
        state["total"] = len(blogger_dirs)

        # 上一轮扫描完成时间：mtime 早于它的博主目录内容没变，只 touch 不遍历
        row = db.query_one("SELECT MAX(last_seen) AS m FROM bloggers")
        prev_scan = float(row["m"]) if row and row["m"] else 0.0
        skipped_unchanged = 0

        log.info("开始扫描: %s（%d 个博主目录，上一轮完成于 %s）",
                 MEDIA_ROOT, len(blogger_dirs),
                 time.strftime("%H:%M:%S", time.localtime(prev_scan))
                 if prev_scan else "从未")

        for i, blogger_dir in enumerate(blogger_dirs):
            state["phase"] = f"扫描博主 {i + 1}/{len(blogger_dirs)}"
            state["done"] = i
            if (i + 1) % 50 == 0 or i + 1 == len(blogger_dirs):
                log.info("扫描进度: %d/%d（新增 %d，跳过未变化 %d）",
                         i + 1, len(blogger_dirs),
                         state["added"], skipped_unchanged)

            row = db.query_one("SELECT id FROM bloggers WHERE dir_name=?",
                               (blogger_dir.name,))

            # 目录 mtime 早于上轮扫描 → 内部无变化：只刷新 last_seen
            if row:
                try:
                    mtime = blogger_dir.stat().st_mtime
                except OSError:
                    mtime = now
                if mtime < prev_scan:
                    # 跳过内部遍历，但博主与其视频记录都要 touch，
                    # 否则增量清理会把该博主的视频全部误删
                    db.execute("UPDATE bloggers SET last_seen=? WHERE id=?",
                               (now, row["id"]), commit=False)
                    db.execute("UPDATE videos SET last_seen=? WHERE blogger_id=?",
                               (now, row["id"]), commit=False)
                    skipped_unchanged += 1
                    if (i + 1) % 50 == 0:
                        db.commit()
                    continue

            # 完整扫描该博主：一次 iterdir，条目复用于海报/元数据/子目录
            try:
                entries = sorted(blogger_dir.iterdir())
            except OSError:
                entries = []
            meta = _metadata_from_entries(entries)
            b_poster = _poster_from_entries(entries)
            if row:
                bid = row["id"]
                db.execute(
                    "UPDATE bloggers SET metadata=?, poster=?, poster_key=?, "
                    "last_seen=? WHERE id=?",
                    (json.dumps(meta, ensure_ascii=False),
                     str(b_poster) if b_poster else None,
                     md5_key(str(b_poster)) if b_poster else None,
                     now, bid), commit=False)
            else:
                cur = db.execute(
                    "INSERT INTO bloggers(dir_name, metadata, poster, poster_key, "
                    "first_seen, last_seen) VALUES(?,?,?,?,?,?)",
                    (blogger_dir.name, json.dumps(meta, ensure_ascii=False),
                     str(b_poster) if b_poster else None,
                     md5_key(str(b_poster)) if b_poster else None, now, now),
                    commit=False)
                bid = cur.lastrowid
                state["added"] += 1

            # 二级视频目录（条目来自博主目录单次 iterdir）
            for sub in entries:
                if not sub.is_dir() or sub.name.startswith("."):
                    continue
                try:
                    sub_entries = sorted(sub.iterdir())
                except OSError:
                    continue
                videos = [f for f in sub_entries
                          if f.is_file() and f.suffix.lower() in VIDEO_EXTS]
                if not videos:
                    continue
                v_poster = _poster_from_entries(sub_entries)
                vmeta = _metadata_from_entries(sub_entries) or meta
                vrow = db.query_one(
                    "SELECT id FROM videos WHERE blogger_id=? AND name=?",
                    (bid, sub.name))
                if vrow:
                    vid = vrow["id"]
                    db.execute(
                        "UPDATE videos SET rel=?, file_name=?, size=?, poster=?, "
                        "poster_key=?, metadata=?, last_seen=? WHERE id=?",
                        (f"{blogger_dir.name}/{sub.name}/{videos[0].name}",
                         videos[0].name, videos[0].stat().st_size,
                         str(v_poster) if v_poster else None,
                         md5_key(str(v_poster)) if v_poster else None,
                         json.dumps(vmeta, ensure_ascii=False), now, vid),
                        commit=False)
                else:
                    db.execute(
                        "INSERT INTO videos(blogger_id, rel, name, file_name, size, "
                        "poster, poster_key, metadata, first_seen, last_seen) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (bid, f"{blogger_dir.name}/{sub.name}/{videos[0].name}",
                         sub.name, videos[0].name, videos[0].stat().st_size,
                         str(v_poster) if v_poster else None,
                         md5_key(str(v_poster)) if v_poster else None,
                         json.dumps(vmeta, ensure_ascii=False), now, now),
                        commit=False)
                    state["added"] += 1

            # 兼容旧结构：/博主ID/文件名.mp4（同一次 iterdir 的条目）
            for f in entries:
                if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
                    continue
                vrow = db.query_one(
                    "SELECT id FROM videos WHERE blogger_id=? AND name=?",
                    (bid, f.stem))
                if vrow:
                    vid = vrow["id"]
                    db.execute(
                        "UPDATE videos SET rel=?, file_name=?, size=?, poster=?, "
                        "poster_key=?, last_seen=? WHERE id=?",
                        (f"{blogger_dir.name}/{f.name}", f.name, f.stat().st_size,
                         str(b_poster) if b_poster else None,
                         md5_key(str(b_poster)) if b_poster else None, now, vid),
                        commit=False)
                else:
                    db.execute(
                        "INSERT INTO videos(blogger_id, rel, name, file_name, size, "
                        "poster, poster_key, metadata, first_seen, last_seen) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (bid, f"{blogger_dir.name}/{f.name}", f.stem, f.name,
                         f.stat().st_size,
                         str(b_poster) if b_poster else None,
                         md5_key(str(b_poster)) if b_poster else None,
                         json.dumps(meta, ensure_ascii=False), now, now),
                        commit=False)
                    state["added"] += 1

            # 每 50 个博主提交一次，减少 NAS 上的 fsync 次数
            if (i + 1) % 50 == 0:
                db.commit()

        db.commit()

        # 增量清理：本轮扫描未被 touch（last_seen 未更新）的记录视为已删除
        state["phase"] = "清理失效记录"
        removed_b = db.execute(
            "DELETE FROM bloggers WHERE last_seen < ?", (now,)).rowcount
        removed_v = db.execute(
            "DELETE FROM videos WHERE last_seen < ?", (now,)).rowcount
        state["removed"] = removed_b + removed_v

        # 更新博主视频计数
        db.execute("UPDATE bloggers SET video_count = "
                   "(SELECT COUNT(*) FROM videos WHERE videos.blogger_id = bloggers.id)")
        state["phase"] = "完成"
        log.info("扫描完成: 新增 %d 条，清理 %d 条，跳过未变化 %d 个博主，耗时 %.1fs",
                 state["added"], state["removed"], skipped_unchanged,
                 time.time() - state["started_at"])
    except Exception as e:
        try:
            db.commit()   # 保留已完成的批量写入（幂等，下次增量补齐）
        except Exception:
            pass
        log.exception("扫描失败")
        state["error"] = str(e)
    finally:
        state["running"] = False
        state["finished_at"] = time.time()
    return {"added": state["added"], "removed": state["removed"],
            "error": state["error"]}


def start_background_scan() -> bool:
    """启动后台扫描线程；已在跑则返回 False。互斥锁避免并发多线程扫描。"""
    import threading
    if not _scan_lock.acquire(blocking=False):
        return False
    if state["running"]:          # 极小概率：线程已启动但尚未更新状态
        _scan_lock.release()
        return False
    threading.Thread(target=_run_and_release, daemon=True).start()
    return True


def _run_and_release() -> None:
    try:
        run_full_scan()
    finally:
        _scan_lock.release()
