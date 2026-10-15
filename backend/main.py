"""FastAPI 主应用：DB 驱动的列表/分页、后台任务、设置持久化、流接口。"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, logbuf, paths, scanner, scheduler, settings
from .config import VIDEO_ROOT
from .emby import EmbyClient, EmbyError, emby_path

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("dy-sort")
logbuf.install()

app = FastAPI(title="DY Sort", version="2.0")


@app.on_event("startup")
def on_startup():
    db.connect()
    log.info("启动: 视频根目录=%s，博主 %d 个，视频 %d 个",
             paths.media_root(), db.count_bloggers(), db.count_videos())
    scanner.start_background_scan()  # 启动即增量扫描
    scheduler.start()                # 定时扫描调度（scan_interval）


@app.get("/api/health")
def health():
    return {"status": "ok", "video_root": str(VIDEO_ROOT),
            "videos": db.count_videos() if db._conn else 0}


# ---------- 实时日志 ----------
@app.get("/api/logs")
def get_logs(after: int = 0):
    """增量返回 seq > after 的日志行（前端轮询）。"""
    return logbuf.fetch(after)


# ---------- 列表（分页，DB 查询） ----------
@app.get("/api/videos")
def list_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(settings.get_int("page_size", 60), ge=1, le=200),
    q: str = "",
    blogger: str = "",
    in_emby: str = "",
):
    where, args = [], []
    if q:
        where.append("(v.name LIKE ? OR b.dir_name LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if blogger:
        where.append("b.dir_name = ?")
        args.append(blogger)
    if in_emby in ("0", "1"):
        where.append("b.in_emby = ?")
        args.append(int(in_emby))

    w = ("WHERE " + " AND ".join(where)) if where else ""
    base = ("FROM videos v JOIN bloggers b ON b.id = v.blogger_id ")

    total = db.query_one(f"SELECT COUNT(*) c {base}{w}", tuple(args))["c"]
    rows = db.query(
        f"""SELECT v.id, v.rel, v.name, v.file_name, v.size, v.poster_key,
                   v.metadata, b.dir_name AS blogger, b.in_emby
            {base}{w}
            ORDER BY b.dir_name, v.name
            LIMIT ? OFFSET ?""",
        tuple(args) + (page_size, (page - 1) * page_size))

    items = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata"] or "{}")
        except Exception:
            pass
        items.append({
            "id": r["id"],
            "key": r["poster_key"],
            "rel": r["rel"],
            "blogger": r["blogger"],
            "name": r["name"],
            "size": r["size"],
            "metadata": meta,
            "in_emby": bool(r["in_emby"]),
            "poster": f"/api/video/{r['id']}/poster" if r["poster_key"] else None,
            "stream": f"/api/video/{r['id']}/stream",
        })

    bloggers = [dict(r) for r in db.query(
        "SELECT dir_name, video_count, in_emby, poster_key FROM bloggers ORDER BY dir_name")]
    for b in bloggers:
        b["in_emby"] = bool(b["in_emby"])
        b["poster"] = f"/api/blogger/{b['dir_name']}/poster" if b["poster_key"] else None
        b.pop("poster_key", None)
    return {"total": total, "page": page, "page_size": page_size,
            "bloggers": bloggers, "items": items}


# ---------- 流接口（功能1：预览） ----------
def _video_row(vid: int):
    row = db.query_one(
        "SELECT v.*, b.dir_name AS blogger, b.in_emby FROM videos v "
        "JOIN bloggers b ON b.id = v.blogger_id WHERE v.id = ?", (vid,))
    if row is None:
        raise HTTPException(404)
    return row


@app.get("/api/video/{vid}")
def video_detail(vid: int):
    """单条视频完整信息（独立预览页用）。"""
    row = _video_row(vid)
    try:
        meta = json.loads(row["metadata"] or "{}")
    except Exception:
        meta = {}
    return {
        "id": row["id"],
        "key": row["poster_key"],
        "rel": row["rel"],
        "blogger": row["blogger"],
        "name": row["name"],
        "size": row["size"],
        "metadata": meta,
        "in_emby": bool(row["in_emby"]),
        "poster": f"/api/video/{row['id']}/poster" if row["poster_key"] else None,
        "stream": f"/api/video/{row['id']}/stream",
    }


@app.get("/api/video/{vid}/poster")
def poster(vid: int):
    row = _video_row(vid)
    if not row["poster_key"]:
        raise HTTPException(404)
    return FileResponse(row["poster"])


@app.get("/api/video/{vid}/stream")
def stream(vid: int):
    row = _video_row(vid)
    if not row["rel"]:
        raise HTTPException(404)
    path = paths.media_root() / row["rel"]
    if not path.is_file():
        raise HTTPException(404, f"文件不存在: {path}")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/blogger/{name}/poster")
def blogger_poster(name: str):
    row = db.query_one("SELECT poster, poster_key FROM bloggers WHERE dir_name=?",
                       (name,))
    if row is None or not row["poster_key"]:
        raise HTTPException(404)
    return FileResponse(row["poster"])


# ---------- 目录绑定 ----------
@app.get("/api/dirs")
def list_dirs():
    """浏览 /data 下的子目录（供绑定短视频根目录）。"""
    if not VIDEO_ROOT.is_dir():
        return JSONResponse({"error": f"/data 未挂载: {VIDEO_ROOT}"}, status_code=400)
    dirs = []
    for d in sorted(VIDEO_ROOT.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            try:
                n = sum(1 for x in d.iterdir() if x.is_dir())
            except OSError:
                n = 0
            dirs.append({"name": d.name, "subdirs": n})
    return {"root": str(VIDEO_ROOT), "current": settings.get("media_subdir"),
            "dirs": dirs}


class BindRequest(BaseModel):
    media_subdir: str = ""      # 空串 = 绑定 /data 根


@app.post("/api/bind")
def bind_dir(req: BindRequest):
    """绑定短视频根目录（/data 下的子目录），换绑后清空索引并重扫。"""
    sub = req.media_subdir.strip("/\\")
    try:
        paths.validate_subdir(sub)
    except paths.BindError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    old = settings.get("media_subdir").strip("/\\")
    settings.set_many({"media_subdir": sub})
    if sub != old:
        db.reset_index()          # 换绑后旧索引失效，重建
        scanner.start_background_scan()
    return {"bound": sub or "/", "rescan": sub != old}


# ---------- 扫描 ----------
@app.post("/api/scan")
def start_scan():
    ok = scanner.start_background_scan()
    return {"started": ok} if ok else JSONResponse(
        {"started": False, "error": "扫描已在进行中"}, status_code=409)


@app.get("/api/scan/state")
def scan_state():
    """扫描状态；scheduler 字段给出定时扫描配置与下次触发时间。"""
    return {**scanner.scan_state(), "scheduler": scheduler.status_info()}


# ---------- Emby（功能2/3/5） ----------
@app.get("/api/emby/libraries")
def emby_libraries():
    try:
        c = EmbyClient()
        libs = c.list_dy_libraries()
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"连接 Emby 失败: {e}"}, status_code=502)
    return {"libraries": libs}


@app.post("/api/emby/sync")
def emby_sync():
    """同步 Emby 实际库路径到本地映射（手工在 Emby 添加的目录也会登记）。"""
    try:
        n = EmbyClient().sync_library_paths_to_db()
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"连接 Emby 失败: {e}"}, status_code=502)
    return {"paths": n}


class EmbyAddRequest(BaseModel):
    bloggers: list[str] | None = None   # 博主ID列表；空 = 全部未加入的
    library_name: str | None = None


@app.post("/api/emby/add")
def emby_add(req: EmbyAddRequest):
    names = req.bloggers
    if not names:
        rows = db.query("SELECT dir_name FROM bloggers WHERE in_emby=0 "
                        "ORDER BY dir_name")
        names = [r["dir_name"] for r in rows]
    if not names:
        return {"library": None, "added": [], "skipped": []}
    try:
        result = EmbyClient().add_bloggers(names, req.library_name)
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("加入 Emby 失败")
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)
    return result


@app.get("/api/emby/map")
def emby_map():
    """本地维护的 媒体库路径 ↔ 博主目录 对应关系。"""
    rows = db.query("SELECT path, blogger, library, added_at FROM emby_map "
                    "ORDER BY blogger")
    return {"map": [dict(r) for r in rows]}


@app.get("/api/emby/overview")
def emby_overview():
    """每个 Emby 媒体库对应的本地目录概览（首页展示用）。

    把 Emby 库路径反向映射回容器内本地目录，并统计已入库博主数。
    本地匹配：Emby 路径末段 == 博主目录名。"""
    local_root = str(paths.media_root()).replace("\\", "/").rstrip("/")
    blogger_counts = {r["dir_name"]: r["video_count"] for r in
                      db.query("SELECT dir_name, video_count FROM bloggers")}

    def emby_to_local(p: str) -> str:
        """Emby 侧路径 -> 容器内本地路径（能映射则映射，否则原样返回）。"""
        ep = p.replace("\\", "/").rstrip("/")
        base = settings.get("host_video_root").replace("\\", "/").rstrip("/")
        if base and ep.lower().startswith(base.lower()):
            return local_root + ep[len(base):]
        return ep

    try:
        libs = EmbyClient().list_dy_libraries()
    except Exception as e:
        return {"error": f"连接 Emby 失败: {e}"}

    out = []
    for lib in libs:
        dirs = []
        for p in lib.get("paths", []):
            local = emby_to_local(p)
            seg = p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            dirs.append({
                "emby_path": p,
                "local_path": local,
                "blogger": seg,
                "videos": blogger_counts.get(seg),
            })
        out.append({"name": lib["name"], "dirs": dirs})
    return {"media_root": local_root, "libraries": out}


# ---------- 删除（可选启用，严格边界，见 cleaner.py） ----------
from .cleaner import (DeleteDisabled, DeleteError, delete_blogger,
                      delete_bloggers, delete_video, preview_blogger,
                      preview_video)


def _del_error(e: DeleteError):
    code = 403 if isinstance(e, DeleteDisabled) else 400
    return JSONResponse({"error": str(e)}, status_code=code)


@app.get("/api/delete/video/{vid}/preview")
def delete_video_preview(vid: int):
    try:
        return preview_video(vid)
    except DeleteError as e:
        return _del_error(e)


@app.get("/api/delete/blogger/{name}/preview")
def delete_blogger_preview(name: str):
    try:
        return preview_blogger(name)
    except DeleteError as e:
        return _del_error(e)


class DeleteVideoRequest(BaseModel):
    confirm: bool = False


@app.delete("/api/video/{vid}")
def api_delete_video(vid: int, req: DeleteVideoRequest):
    """删除单个视频（confirm=true 才执行）。"""
    try:
        result = delete_video(vid, confirm=req.confirm)
    except DeleteError as e:
        return _del_error(e)
    return {**result, "video_left": db.count_videos()}


class DeleteBloggerRequest(BaseModel):
    confirm: bool = False
    remove_from_emby: bool = True


@app.delete("/api/blogger/{name}")
def api_delete_blogger(name: str, req: DeleteBloggerRequest):
    """删除整个博主（目录 + 全部视频 + Emby 路径，confirm=true 才执行）。"""
    try:
        result = delete_blogger(name, confirm=req.confirm,
                                remove_from_emby=req.remove_from_emby)
    except DeleteError as e:
        return _del_error(e)
    return {**result, "video_left": db.count_videos()}


class DeleteBloggersRequest(BaseModel):
    bloggers: list[str]
    confirm: bool = False
    remove_from_emby: bool = True


@app.post("/api/delete/bloggers")
def api_delete_bloggers(req: DeleteBloggersRequest):
    """批量删除博主（1~50 个，逐个执行，失败项跳过并报告）。"""
    try:
        return delete_bloggers(req.bloggers, confirm=req.confirm,
                               remove_from_emby=req.remove_from_emby)
    except DeleteError as e:
        return _del_error(e)


# ---------- 设置（持久化） ----------
class SettingsRequest(BaseModel):
    values: dict[str, str]


@app.get("/api/settings")
def get_settings():
    return settings.all_settings()


@app.post("/api/settings")
def save_settings(req: SettingsRequest):
    settings.set_many(req.values)
    return {"ok": True, "settings": settings.all_settings()}


# ---------- 前端 ----------
import os
if os.path.isdir("web"):
    app.mount("/", StaticFiles(directory="web", html=True), name="web")
