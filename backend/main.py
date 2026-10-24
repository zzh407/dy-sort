"""FastAPI 主应用：DB 驱动的列表/分页、后台任务、设置持久化、流接口。"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import capacity, db, logbuf, paths, scanner, scheduler, settings
from . import style as style_lib
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
    try:
        # 把所有配置项补齐写进 config/config.yaml（含空值；关键词键做一次性迁移）
        added = settings.persist_all()
        if added["added"]:
            log.info("配置补齐: %d 个键写入 config.yaml（%s）",
                     len(added["added"]), ", ".join(added["added"][:6])
                     + ("…" if len(added["added"]) > 6 else ""))
    except Exception as e:  # noqa: BLE001
        log.warning("配置持久化失败: %s", e)
    log.info("启动: 视频根目录=%s，博主 %d 个，视频 %d 个",
             paths.media_root(), db.count_bloggers(), db.count_videos())
    try:
        style_lib.seed_from_libraries()   # 按 Emby 库名做零成本预标注
    except Exception as e:  # noqa: BLE001 - 预标注失败不影响启动
        log.warning("风格预标注失败: %s", e)
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
        "SELECT b.dir_name, b.video_count, b.in_emby, b.poster_key, "
        "       COALESCE(s.style,'') AS style "
        "FROM bloggers b LEFT JOIN blogger_style s ON s.blogger = b.dir_name "
        "ORDER BY b.dir_name")]
    for b in bloggers:
        b["in_emby"] = bool(b["in_emby"])
        b["poster"] = f"/api/blogger/{b['dir_name']}/poster" if b["poster_key"] else None
        b.pop("poster_key", None)
    return {"total": total, "page": page, "page_size": page_size,
            "bloggers": bloggers, "items": items}


# ---------- 博主列表与穿衣风格 ----------
@app.get("/api/bloggers")
def api_bloggers(q: str = "", style: str = "", page: int = Query(1, ge=1),
                 page_size: int = Query(200, ge=1, le=1000)):
    """博主列表（含风格标签），预览页左侧列表用。"""
    if not db.query_one("SELECT COUNT(*) c FROM blogger_style")["c"]:
        try:
            style_lib.seed_from_libraries()   # 首次访问时补一次库名预标注
        except Exception as e:  # noqa: BLE001
            log.warning("风格预标注失败: %s", e)
    return style_lib.blogger_page(q, style, page, page_size)


@app.get("/api/styles/state")
def api_style_state():
    """识别任务进度与配置状态。"""
    return style_lib.state()


class StyleRunRequest(BaseModel):
    bloggers: list[str] | None = None
    force: bool = False
    mode: str = "quick"           # quick = 每人随机 10 个视频；full = 全部视频
    sample: int | None = None     # 覆盖 quick 的抽样数（至少 10）
    batch: int | None = None      # 每次请求几张图


@app.post("/api/styles/run")
def api_style_run(req: StyleRunRequest):
    """后台跑大模型识别：快速（每人 10 个）/ 全量（所有视频）。"""
    return style_lib.run_batch(req.bloggers, req.force, req.mode,
                               req.sample, req.batch)


@app.post("/api/styles/stop")
def api_style_stop():
    """停止当前识别任务。"""
    return style_lib.stop()


@app.post("/api/styles/seed")
def api_style_seed():
    """按 Emby 库名 + 博主名做一次零成本预标注（不覆盖人工/已识别结果）。"""
    return {"library": style_lib.seed_from_libraries(),
            "name": style_lib.seed_from_blogger_names(),
            "body": style_lib.seed_body_from_names(),
            "summary": style_lib.summary(),
            "body_summary": style_lib.body_summary()}


class StyleSetRequest(BaseModel):
    blogger: str
    style: str = ""                  # 单标签（兼容旧调用）
    styles: list[str] | None = None   # 风格多标签，按给定顺序保存
    bodies: list[str] | None = None   # 身材多标签


@app.post("/api/styles/set")
def api_style_set(req: StyleSetRequest):
    """人工修正某个博主的风格 / 身材标签（都支持多标签）。"""
    if not req.blogger.strip():
        return JSONResponse({"error": "缺少 blogger 参数"}, status_code=400)
    name = req.blogger.strip()
    wanted = req.styles if req.styles is not None else [
        x for x in str(req.style).split(",") if x.strip()]
    picked: list[str] = []
    for raw in wanted:
        label = style_lib.normalize(raw)
        if not label:
            return JSONResponse({"error": f"标签不在可选列表里：{raw}",
                                 "labels": style_lib.labels()}, status_code=400)
        if label not in picked:
            picked.append(label)
    bodies: list[str] = []
    for raw in req.bodies or []:
        label = style_lib.normalize_body(raw)
        if not label:
            return JSONResponse({"error": f"身材标签不在可选列表里：{raw}",
                                 "body_labels": style_lib.body_labels()},
                                status_code=400)
        if label not in bodies:
            bodies.append(label)
    if not picked and not bodies:
        style_lib.clear_style(name)
        return {"blogger": name, "style": "", "labels": [], "body": "",
                "body_labels": [], "cleared": True}
    labels_list = [{"style": s, "count": 1, "ratio": 1.0} for s in picked]
    body_list = [{"style": s, "count": 1, "ratio": 1.0} for s in bodies]
    style_lib.set_style(name, picked[0] if picked else "", "manual", 1.0, "人工标注",
                        labels_list=labels_list,
                        body=bodies[0] if bodies else "",
                        body_labels_list=body_list)
    return {"blogger": name, "style": picked[0] if picked else "",
            "labels": labels_list, "body": bodies[0] if bodies else "",
            "body_labels": body_list, "source": "manual"}


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
    st = style_lib.get_style(row["blogger"])
    return {
        "id": row["id"],
        "key": row["poster_key"],
        "rel": row["rel"],
        "blogger": row["blogger"],
        "blogger_style": st["style"],
        "blogger_style_labels": st["labels"],
        "blogger_body": st["body"],
        "blogger_body_labels": st["body_labels"],
        "blogger_style_source": st["source"],
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
def _limits() -> tuple[int, bool]:
    return (max(1, settings.get_int("emby_library_max_videos", 2000) or 2000),
            settings.get_bool("emby_auto_library", True))


@app.get("/api/emby/libraries")
def emby_libraries():
    """DY_ 媒体库列表，附视频数与容量（前端选库弹窗展示）。"""
    try:
        c = EmbyClient()
        libs = c.list_dy_libraries()
        try:
            counts = c.library_counts(libs)
        except EmbyError as e:
            log.warning("读取媒体库视频数失败: %s", e)
            counts = {}
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"连接 Emby 失败: {e}"}, status_code=502)
    limit, auto = _limits()
    names = [l["name"] for l in libs]
    out = []
    for lib in libs:
        v = counts.get(lib["name"])
        out.append({"name": lib["name"], "paths": lib["paths"], "videos": v,
                    "limit": limit, "over": bool(v is not None and v > limit),
                    "family": capacity.split_family(lib["name"], names)[0]})
    return {"libraries": out, "limit": limit, "auto": auto}


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


class EmbyMoveRequest(BaseModel):
    blogger: str
    to_library: str | None = None    # 目标库名；留空或 create_new 时新建序号库
    create_new: bool = False


@app.post("/api/emby/move")
def emby_move(req: EmbyMoveRequest):
    """把博主目录整体移动到另一个 DY_ 媒体库。

    目录是最小单位：该博主的所有视频一起走，不会被拆到两个库。
    create_new=true（或未指定 to_library）时新建同家族的下一个序号库。"""
    if not req.blogger or not req.blogger.strip():
        return JSONResponse({"error": "缺少 blogger 参数"}, status_code=400)
    try:
        result = EmbyClient().move_blogger(req.blogger.strip(),
                                           req.to_library, req.create_new)
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("移动目录失败")
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)
    return result


@app.get("/api/emby/rebalance/preview")
def emby_rebalance_preview(libraries: str = ""):
    """整理预览：哪些目录会从超限库整体迁到哪个新建序号库（不执行）。

    libraries 为空 = 所有 DY_ 库（一键整理）；也可用逗号分隔只算指定库。"""
    names = [n.strip() for n in libraries.split(",") if n.strip()]
    try:
        return EmbyClient().rebalance_plan(names or None)
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("整理预览失败")
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)


class RebalanceRequest(BaseModel):
    confirm: bool = False
    libraries: list[str] | None = None


@app.post("/api/emby/rebalance")
def emby_rebalance(req: RebalanceRequest):
    """执行整理：把超限库里放不下的目录整体迁到新建序号库。需 confirm=true。

    执行前会重新计算一次方案（而非沿用预览），保证库名/计数都是最新的。"""
    if not req.confirm:
        return JSONResponse({"error": "缺少二次确认 (confirm=true)"}, status_code=400)
    try:
        client = EmbyClient()
        plan = client.rebalance_plan(req.libraries)
        return client.rebalance_apply(plan)
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("整理媒体库失败")
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)


# ---------- 目录映射指引 ----------
@app.get("/api/mapping/diagnose")
def mapping_diagnose():
    """映射自检：三步状态、库路径反解校验、host_video_root 建议（只读）。"""
    try:
        return EmbyClient().diagnose_mapping()
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("映射自检失败")
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)


class AutoFixRequest(BaseModel):
    apply: bool = False


@app.post("/api/mapping/autofix")
def mapping_autofix(req: AutoFixRequest):
    """一键校准 Emby 侧根：默认只回建议，apply=true 才写入 config.yaml。"""
    try:
        diag = EmbyClient().diagnose_mapping()
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)
    suggest = diag["detected"]["root"]
    if not suggest:
        return {"applied": False, "reason": "没有可推断的目标（先在「浏览」页加入一次再试）",
                "diagnosis": diag}
    if not req.apply:
        return {"applied": False, "suggest": suggest, "diagnosis": diag}
    settings.set_many({"host_video_root": suggest})
    log.info("映射一键校准: host_video_root=%s", suggest)
    return {"applied": True, "host_video_root": suggest,
            "diagnosis": EmbyClient().diagnose_mapping()}


@app.get("/api/emby/map")
def emby_map():
    """本地维护的 媒体库路径 ↔ 博主目录 对应关系。"""
    rows = db.query("SELECT path, blogger, library, added_at FROM emby_map "
                    "ORDER BY blogger")
    return {"map": [dict(r) for r in rows]}


class EmbyRemoveRequest(BaseModel):
    blogger: str
    confirm: bool = False


@app.post("/api/emby/remove")
def emby_remove(req: EmbyRemoveRequest):
    """把博主目录从 Emby 媒体库移除（只删库路径条目，不动本地文件）。

    与删除博主不同：这是可逆操作，重新「加入 Emby」即可恢复；
    本地索引与磁盘文件不受影响。"""
    if not req.blogger or not req.blogger.strip():
        return JSONResponse({"error": "缺少 blogger 参数"}, status_code=400)
    if not req.confirm:
        return JSONResponse({"error": "缺少二次确认 (confirm=true)"}, status_code=400)
    name = req.blogger.strip()
    try:
        removed = EmbyClient().remove_blogger_path(name)
    except EmbyError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.exception("移出 Emby 失败")
        return JSONResponse({"error": f"Emby 请求失败: {e}"}, status_code=502)
    if not removed:
        return JSONResponse({"error": f"该目录不在任何 DY_ 媒体库中: {name}"},
                            status_code=404)
    try:
        EmbyClient().sync_library_paths_to_db()   # 刷新 bloggers.in_emby
    except Exception as e:
        log.warning("移出后同步 Emby 状态失败: %s", e)
    return {"removed": name, "emby_removed": True}


@app.get("/api/emby/overview")
def emby_overview():
    """每个 Emby 媒体库对应的本地目录概览（首页展示用）。

    把 Emby 库路径反向映射回容器内本地目录，并统计已入库博主数。
    本地匹配：Emby 路径末段 == 博主目录名。附带各库视频数与容量上限。"""
    limit, auto = _limits()
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
        client = EmbyClient()
        libs = client.list_dy_libraries()
        try:
            counts = client.library_counts(libs)
        except EmbyError as e:
            log.warning("读取媒体库视频数失败: %s", e)
            counts = {}
    except Exception as e:
        return {"error": f"连接 Emby 失败: {e}"}

    names = [l["name"] for l in libs]
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
        total = counts.get(lib["name"])
        out.append({"name": lib["name"], "dirs": dirs,
                    "videos": total, "limit": limit,
                    "over": bool(total is not None and total > limit),
                    "family": capacity.split_family(lib["name"], names)[0]})
    return {"media_root": local_root, "libraries": out,
            "limit": limit, "auto": auto}


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
@app.middleware("http")
async def no_cache_static(request, call_next):
    """静态页面/资源禁用缓存：前端无构建无版本号，避免改版后浏览器仍用旧 JS。
    no-store：任何情况下都不得从缓存取（no-cache 的启发式缓存旧条目仍可能残留）。"""
    resp = await call_next(request)
    p = request.url.path
    if not p.startswith("/api/") and (
            p == "/" or "." in p.rsplit("/", 1)[-1]):
        resp.headers["Cache-Control"] = "no-store"
    return resp

import os
if os.path.isdir("web"):
    app.mount("/", StaticFiles(directory="web", html=True), name="web")
