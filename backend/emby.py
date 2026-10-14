"""Emby API 客户端：只读 DY_ 前缀媒体库，路径映射维护在 SQLite emby_map。"""
from __future__ import annotations

import logging
import time

import httpx

from . import db, settings

log = logging.getLogger("dy-sort.emby")


class EmbyError(Exception):
    pass


def emby_path(blogger: str) -> str:
    """博主目录对应的 Emby 侧路径（基于绑定的短视频根目录）。"""
    from .paths import media_root
    base = settings.get("host_video_root")
    if base:
        # Emby 侧根 = host_video_root + 绑定子目录部分
        sub = settings.get("media_subdir").strip("/\\")
        root = base.rstrip("/\\")
        if sub:
            root = f"{root}/{sub}"
    else:
        root = str(media_root()).rstrip("/\\")
    return f"{root}/{blogger}"


class EmbyClient:
    def __init__(self):
        url = settings.get("emby_url")
        if not url:
            raise EmbyError("未配置 Emby 地址，请在设置中填写")
        self.base = url.rstrip("/")
        self.api_key = settings.get("emby_api_key")
        if not self.api_key:
            raise EmbyError("未配置 Emby API Key，请在设置中填写")

    def _headers(self) -> dict:
        return {"X-Emby-Token": self.api_key}

    def _get(self, path: str) -> httpx.Response:
        return httpx.get(self.base + path, headers=self._headers(), timeout=15)

    def _post(self, path: str, **kwargs) -> httpx.Response:
        return httpx.post(self.base + path, headers=self._headers(),
                          timeout=kwargs.pop("timeout", 30), **kwargs)

    # ---------- 媒体库 ----------
    def list_dy_libraries(self) -> list[dict]:
        """只返回前缀匹配的媒体库（默认 DY_）。"""
        prefix = settings.get("emby_library_prefix", "DY_")
        r = self._get("/Library/VirtualFolders")
        r.raise_for_status()
        out = []
        for lib in r.json():
            if not lib.get("Name", "").startswith(prefix):
                continue
            out.append({
                "id": lib.get("ItemId"),
                "guid": lib.get("Guid") or lib.get("ItemId"),
                "name": lib.get("Name"),
                "paths": lib.get("Locations", []),
            })
        return out

    def all_dy_paths(self) -> set[str]:
        """所有 DY_ 库的路径集合（归一化）。"""
        paths: set[str] = set()
        for lib in self.list_dy_libraries():
            for p in lib.get("paths", []):
                paths.add(p.replace("\\", "/").rstrip("/"))
        return paths

    def add_path_to_library(self, library_id: str, path: str,
                        refresh: bool = True) -> None:
        """向媒体库添加一个路径。

        Emby 4.10 的Library.AddMediaPath 要求用库的 **Guid** 作为 Id
        （传名称或数字 ItemId 会得到 500 Unrecognized Guid format）。
        refresh=False 用于批量添加，最后统一刷新一次。
        """
        p = path.replace("/", "\\") if "\\" in path else path
        r = self._post("/Library/VirtualFolders/Paths",
                       json={"Id": library_id,
                             "Path": p,
                             "PathInfo": {"Path": p},
                             "RefreshLibrary": refresh})
        if r.status_code >= 400:
            detail = (r.text or "").strip()[:200]
            raise EmbyError(f"Emby 拒绝添加路径（HTTP {r.status_code}）"
                            + (f"：{detail}" if detail else "")
                            + f"，库 Id={library_id}")

    def refresh_library(self) -> None:
        """触发 Emby 库刷新（批量添加路径后调用一次即可）。"""
        try:
            self._post("/Library/Refresh", json={})
        except Exception as e:
            log.warning("触发 Emby 刷新失败: %s", e)

    def sync_library_paths_to_db(self) -> int:
        """把 Emby 中 DY_ 库的实际路径同步到 emby_map，并刷新 bloggers.in_emby。

        返回映射条数。Emby 侧手工添加的目录也会被登记。"""
        try:
            paths = self.all_dy_paths()
        except Exception as e:
            raise EmbyError(f"连接 Emby 失败: {e}")

        now = time.time()
        for p in paths:
            blogger = p.rstrip("/").rsplit("/", 1)[-1]
            db.execute(
                "INSERT INTO emby_map(path, blogger, added_at) VALUES(?,?,?) "
                "ON CONFLICT(path) DO NOTHING", (p, blogger, now))

        # 博主目录是否已在库中：Emby 路径末段 == 博主目录名，或精确匹配
        local_bloggers = {r["dir_name"]: r["id"] for r in
                          db.query("SELECT id, dir_name FROM bloggers")}
        by_blogger: dict[str, set[str]] = {}
        for p in paths:
            seg = p.rstrip("/").rsplit("/", 1)[-1].lower()
            by_blogger.setdefault(seg, set()).add(p)
        for name, bid in local_bloggers.items():
            emby_paths = by_blogger.get(name.lower(), set())
            if emby_paths:
                db.execute("UPDATE bloggers SET in_emby=1 WHERE id=?", (bid,))
                for p in emby_paths:
                    db.execute(
                        "INSERT INTO emby_map(path, blogger, added_at) VALUES(?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET blogger=excluded.blogger",
                        (p, name, now))
            else:
                db.execute("UPDATE bloggers SET in_emby=0 WHERE id=?", (bid,))
        log.info("同步 Emby 库路径: %d 条", len(paths))
        return len(paths)

    def resolve_library(self, library_name: str | None = None) -> dict:
        """选定要操作的媒体库：由调用方（网页弹窗）手动指定；
        未指定时仅在唯一匹配时自动选取。"""
        libs = self.list_dy_libraries()
        if not libs:
            raise EmbyError(f"未找到 {settings.get('emby_library_prefix', 'DY_')} 开头的媒体库")
        if library_name:
            lib = next((l for l in libs if l["name"] == library_name), None)
            if lib is None:
                raise EmbyError(f"媒体库不存在或前缀不匹配: {library_name}")
            return lib
        if len(libs) > 1:
            raise EmbyError("存在多个 DY_ 媒体库，请手动选择目标库: "
                            + ", ".join(l["name"] for l in libs))
        return libs[0]

    def add_bloggers(self, bloggers: list[str], library_name: str | None = None) -> dict:
        """把博主目录加入媒体库，并同步 emby_map 与 in_emby 状态。"""
        lib = self.resolve_library(library_name)

        existing = self.all_dy_paths()
        added, skipped = [], []
        for b in bloggers:
            p = emby_path(b)
            norm = p.replace("\\", "/").rstrip("/")
            if norm in existing:
                skipped.append(b)
                continue
            self.add_path_to_library(lib["guid"], p, refresh=False)
            existing.add(norm)          # 防止同一批次重复加入
            added.append(b)
            db.execute(
                "INSERT INTO emby_map(path, blogger, library, added_at) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET library=excluded.library",
                (norm, b, lib["name"], time.time()))

        if added:
            self.refresh_library()      # 批量添加后统一刷新一次
        self.sync_library_paths_to_db()
        log.info("加入 Emby: 库=%s 新增 %d 个，跳过 %d 个",
                 lib["name"], len(added), len(skipped))
        return {"library": lib["name"], "added": added, "skipped": skipped}
