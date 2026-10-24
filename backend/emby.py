"""Emby API 客户端：只读 DY_ 前缀媒体库，路径映射维护在 SQLite emby_map。

媒体库容量管理（见 capacity.py）：
- 加入目录时按 ``emby_library_max_videos`` 自动分流，
  超出上限就新建同名前缀的序号库（DY_瑜伽裤 → DY_瑜伽裤2 → DY_瑜伽裤3）；
- 目录是最小单位，任何操作都不拆分同一个博主目录。
"""
from __future__ import annotations

import logging
import time

import httpx

from . import capacity, db, settings

log = logging.getLogger("dy-sort.emby")


class EmbyError(Exception):
    pass


def _prefix() -> str:
    return settings.get("emby_library_prefix", "DY_") or "DY_"


def _limit() -> int:
    return max(1, settings.get_int("emby_library_max_videos", 2000) or 2000)


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").rstrip("/")


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


def _db_weights() -> dict[str, int]:
    """博主目录名 -> 已索引视频数（规划容量时的权重）。"""
    try:
        rows = db.query("SELECT dir_name, video_count FROM bloggers")
    except Exception:  # noqa: BLE001 - 索引未就绪时按 0 处理
        return {}
    return {r["dir_name"]: int(r["video_count"] or 0) for r in rows}


def _db_counts() -> dict[str, int]:
    """Emby 计数不可用时的兜底估算：按 emby_map.library 汇总本地视频数。"""
    try:
        rows = db.query(
            "SELECT m.library AS library, SUM(b.video_count) AS c "
            "FROM emby_map m JOIN bloggers b ON b.dir_name = m.blogger "
            "WHERE m.library IS NOT NULL GROUP BY m.library")
    except Exception:  # noqa: BLE001
        return {}
    return {r["library"]: int(r["c"] or 0) for r in rows}


def _merge_counts(counts: dict[str, int]) -> dict[str, int]:
    """Emby 实测计数与本地索引估算取较大值。

    新建库或刚加入目录时 Emby 还没扫描完，只信 Emby 会把刚填的库当成空库，
    从而继续往里塞到超过上限；反过来本地索引可能有未同步的残留，
    所以只在两种口径都在时取大值，任一侧缺失时用另一侧。"""
    merged = dict(counts)
    for name, value in _db_counts().items():
        merged[name] = max(int(merged.get(name, 0) or 0), int(value or 0))
    return merged


def _size_ratio(counts: dict[str, int],
                folders_by_lib: dict[str, list[tuple[str, int]]]) -> float:
    """Emby 条目数 / 本地视频数的换算比例。

    本地索引按「视频目录」计数，Emby 按文件计数（一个目录里可能有多集/多文件），
    实测 Emby 普遍是本地数的 1.0~1.25 倍。排容量时若直接用本地数，
    会少算 10%~20%，库会悄悄超限，所以按实测比例放大待加入目录的权重。
    比例只放大不缩小，上限 2.0（异常数据保护）。"""
    emby_sum = sum(int(v or 0) for v in counts.values())
    local_sum = sum(int(v or 0) for rows in folders_by_lib.values() for _, v in rows)
    if emby_sum > 0 and local_sum > 0:
        ratio = emby_sum / local_sum
        if 1.0 < ratio <= 2.0:
            return ratio
    return 1.0


def _scale(folders: list[tuple[str, int]], ratio: float) -> list[tuple[str, int]]:
    if ratio <= 1.0:
        return folders
    return [(name, int(round(v * ratio))) for name, v in folders]


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

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        return httpx.get(self.base + path, headers=self._headers(),
                         params=params, timeout=15)

    def _post(self, path: str, **kwargs) -> httpx.Response:
        return httpx.post(self.base + path, headers=self._headers(),
                          timeout=kwargs.pop("timeout", 30), **kwargs)

    def _delete(self, path: str, **kwargs) -> httpx.Response:
        return httpx.delete(self.base + path, headers=self._headers(),
                            timeout=kwargs.pop("timeout", 30), **kwargs)

    # ---------- 媒体库 ----------
    def list_dy_libraries(self) -> list[dict]:
        """只返回前缀匹配的媒体库（默认 DY_）。"""
        prefix = _prefix()
        r = self._get("/Library/VirtualFolders")
        r.raise_for_status()
        out = []
        for lib in r.json():
            name = lib.get("Name", "")
            if not name.startswith(prefix):
                continue
            out.append({
                "id": lib.get("ItemId"),
                "item_id": lib.get("ItemId"),
                "guid": lib.get("Guid") or lib.get("ItemId"),
                "name": name,
                "paths": lib.get("Locations", []),
                "collection_type": lib.get("CollectionType") or "",
                "options": lib.get("LibraryOptions") or {},
            })
        return out

    def all_dy_paths(self) -> set[str]:
        """所有 DY_ 库的路径集合（归一化）。"""
        paths: set[str] = set()
        for lib in self.list_dy_libraries():
            for p in lib.get("paths", []):
                paths.add(_norm(p))
        return paths

    @staticmethod
    def _find_location(lib: dict, blogger: str) -> str | None:
        """在库的 Locations 里找到某个博主目录的原始路径字符串。"""
        want = blogger.lower()
        for p in lib.get("paths", []):
            if capacity.path_basename(p).lower() == want:
                return p
        return None

    def library_counts(self, libs: list[dict] | None = None) -> dict[str, int]:
        """每个 DY_ 库的视频数（Emby 实测，Movie/Video 条目）。"""
        out: dict[str, int] = {}
        for lib in (libs if libs is not None else self.list_dy_libraries()):
            iid = lib.get("item_id") or lib.get("id")
            r = self._get("/Items", params={
                "ParentId": iid, "Recursive": "true", "Limit": 0,
                "IncludeItemTypes": "Video,Movie"})
            if r.status_code >= 400:
                raise EmbyError(f"读取媒体库「{lib['name']}」视频数失败"
                                f"（HTTP {r.status_code}）")
            out[lib["name"]] = int(r.json().get("TotalRecordCount") or 0)
        return out

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

    def remove_path(self, lib: dict, path: str) -> None:
        """从媒体库移除一个路径（只删库路径条目，不动磁盘文件）。"""
        body = {"Id": lib.get("guid"), "Path": path, "RefreshLibrary": False}
        r = self._post("/Library/VirtualFolders/Paths/Delete", json=body)
        if r.status_code < 400:
            return
        # 旧版 Emby 没有 Paths/Delete，回退到 DELETE + 查询参数
        detail = f"HTTP {r.status_code}" + (f"：{(r.text or '').strip()[:200]}" if r.text else "")
        for extra, refresh in (({"Id": lib.get("guid")}, "true"),
                               ({"name": lib["name"]}, "true")):
            rr = self._delete("/Library/VirtualFolders/Paths",
                              params={**extra, "path": path,
                                      "refreshLibrary": refresh})
            if rr.status_code < 400:
                return
            detail = f"HTTP {rr.status_code}" + (f"：{(rr.text or '').strip()[:200]}"
                                                if rr.text else "")
        raise EmbyError(f"Emby 拒绝移除路径 (库 {lib['name']}): {detail}")

    def create_library(self, name: str, collection_type: str, paths: list[str],
                       options: dict | None = None, refresh: bool = False) -> None:
        """新建媒体库；沿用来源库的 LibraryOptions（元数据语言等）以便行为一致。"""
        opts = dict(options or {})
        if opts:
            opts["PathInfos"] = [{"Path": p} for p in paths]
        body = {"Name": name, "CollectionType": collection_type or "",
                "Paths": list(paths), "RefreshLibrary": refresh}
        if opts:
            body["LibraryOptions"] = opts
        r = self._post("/Library/VirtualFolders", json=body)
        if r.status_code >= 400:
            detail = (r.text or "").strip()[:200]
            raise EmbyError(f"Emby 拒绝新建媒体库「{name}」（HTTP {r.status_code}）"
                            + (f"：{detail}" if detail else ""))
        log.info("Emby 新建媒体库: %s（%d 个目录）", name, len(paths))

    def create_library_unique(self, root: str, collection_type: str,
                              paths: list[str], options: dict | None = None,
                              taken: set[str] | None = None) -> str:
        """新建媒体库，名字被占用就自动 +1 重试，返回实际建成的名字。

        每次重试都重新读一遍 Emby 的库列表，所以「计划里的名字刚好被别人
        建过 / 之前整理留下的序号」都会自动跳过，不会撞名。"""
        taken = set(taken or ())
        for _ in range(8):
            names = {l["name"] for l in self.list_dy_libraries()} | taken
            name = capacity.next_name(root, names)
            try:
                self.create_library(name, collection_type, paths, options)
                return name
            except EmbyError as e:
                msg = str(e).lower()
                if ("exist" in msg or "duplicate" in msg
                        or "已存在" in msg or "同名" in msg):
                    log.warning("媒体库名「%s」已存在，自动换下一个序号", name)
                    taken.add(name)
                    continue
                raise
        raise EmbyError(f"无法为「{root}」找到可用的媒体库名（连续 8 次撞名）")

    def refresh_library(self) -> None:
        """触发 Emby 库刷新（批量添加路径后调用一次即可）。"""
        try:
            self._post("/Library/Refresh", json={})
        except Exception as e:
            log.warning("触发 Emby 刷新失败: %s", e)

    # ---------- 加入 / 移动 / 整理 ----------
    def plan_add(self, libs: list[dict], target: dict,
                 folders: list[tuple[str, int]]) -> dict:
        """按容量规划把目录分到目标库及其后的新建序号库。"""
        names = [l["name"] for l in libs]
        root, _ = capacity.split_family(target["name"], names)
        family = [l for l in libs
                  if capacity.split_family(l["name"], names)[0] == root]
        try:
            counts = self.library_counts(family)
        except EmbyError as e:
            log.warning("读取媒体库视频数失败，改用本地索引估算: %s", e)
            counts = {}
        counts = _merge_counts(counts)
        family_names = [l["name"] for l in family]
        folders_by_lib = self.library_folders()
        ratio = _size_ratio({n: counts.get(n, 0) for n in family_names},
                            {n: folders_by_lib.get(n, []) for n in family_names})
        if ratio > 1.0:
            log.info("容量换算比例 %.2f（Emby 条目 / 本地视频）", ratio)
        return capacity.plan_add(target["name"], names, counts,
                                 _scale(folders, ratio), _limit())

    def add_bloggers(self, bloggers: list[str], library_name: str | None = None,
                     auto: bool | None = None) -> dict:
        """把博主目录加入媒体库；开启自动管理时按容量分流到序号库。"""
        libs = self.list_dy_libraries()
        target = self.resolve_library(library_name, libs)

        existing = self.all_dy_paths()
        pending, skipped = [], []
        for b in bloggers:
            if _norm(emby_path(b)) in existing:
                skipped.append(b)
            else:
                pending.append(b)
                existing.add(_norm(emby_path(b)))

        if not pending:
            self.sync_library_paths_to_db()
            return {"library": target["name"], "added": [], "skipped": skipped,
                    "assignments": [], "created": [], "warnings": [],
                    "limit": _limit()}

        if auto is None:
            auto = settings.get_bool("emby_auto_library", True)
        weights = _db_weights()
        folders = [(b, weights.get(b, 0)) for b in pending]

        if auto:
            plan = self.plan_add(libs, target, folders)
        else:
            plan = {"assignments": [{"library": target["name"], "created": False,
                                     "bloggers": pending,
                                     "videos": sum(v for _, v in folders),
                                     "before": 0}],
                    "created": [], "warnings": [],
                    "root": target["name"]}

        by_name = {l["name"]: l for l in libs}
        root = plan.get("root") or target["name"]
        added: list[str] = []
        created_actual: list[str] = []
        for group in plan["assignments"]:
            paths = [emby_path(b) for b in group["bloggers"]]
            if group["created"]:
                # 实际建库时再确认一次名字（可能已被占用），并在返回值里反映真实库名
                actual = self.create_library_unique(
                    root, target.get("collection_type", ""), paths,
                    target.get("options"), taken=set(created_actual))
                created_actual.append(actual)
                group["library"] = actual
            else:
                src = by_name.get(group["library"])
                if src is None:
                    raise EmbyError(f"目标媒体库已不存在: {group['library']}")
                for p in paths:
                    self.add_path_to_library(src["guid"], p, refresh=False)
            now = time.time()
            for b, p in zip(group["bloggers"], paths):
                added.append(b)
                db.execute(
                    "INSERT INTO emby_map(path, blogger, library, added_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                    "library=excluded.library, blogger=excluded.blogger",
                    (_norm(p), b, group["library"], now))

        if added:
            self.refresh_library()      # 批量添加后统一刷新一次
        self.sync_library_paths_to_db()
        log.info("加入 Emby: 目标=%s 新增 %d 个目录（新建库 %s），跳过 %d 个",
                 target["name"], len(added), created_actual or "无", len(skipped))
        return {"library": target["name"], "added": added, "skipped": skipped,
                "assignments": plan["assignments"], "created": created_actual,
                "warnings": plan["warnings"], "limit": _limit()}

    def move_blogger(self, blogger: str, to_library: str | None = None,
                     create_new: bool = False) -> dict:
        """把博主目录整体移动到另一个 DY_ 媒体库（可选自动新建序号库）。"""
        path = emby_path(blogger)
        libs = self.list_dy_libraries()
        names = [l["name"] for l in libs]
        src = next((l for l in libs if self._find_location(l, blogger)), None)
        if src is None:
            raise EmbyError(f"目录「{blogger}」不在任何 DY_ 媒体库中")

        created = False
        if create_new or not to_library:
            root, _ = capacity.split_family(src["name"], names)
            target_name = self.create_library_unique(
                root, src.get("collection_type", ""), [path], src.get("options"))
            created = True
        else:
            dst = next((l for l in libs if l["name"] == to_library), None)
            if dst is None:
                raise EmbyError(f"目标媒体库不存在或前缀不匹配: {to_library}")
            if dst["name"] == src["name"]:
                raise EmbyError(f"「{blogger}」已在媒体库「{dst['name']}」中")
            # 先加后删，避免中途失败导致目录从两个库都消失
            self.add_path_to_library(dst["guid"], path, refresh=False)
            target_name = dst["name"]

        orig = self._find_location(src, blogger)
        self.remove_path(src, orig)
        db.execute("UPDATE emby_map SET library=? WHERE path=?",
                   (target_name, _norm(path)))
        self.refresh_library()
        self.sync_library_paths_to_db()
        log.info("移动目录 %s: %s -> %s%s", blogger, src["name"], target_name,
                 "（新建库）" if created else "")
        return {"moved": blogger, "from": src["name"], "to": target_name,
                "created": created}

    def library_folders(self) -> dict[str, list[tuple[str, int]]]:
        """每个库里的目录及其本地视频数（Emby 路径 -> 目录名）。"""
        weights = _db_weights()
        out: dict[str, list[tuple[str, int]]] = {}
        for lib in self.list_dy_libraries():
            out[lib["name"]] = [
                (capacity.path_basename(p), weights.get(capacity.path_basename(p), 0))
                for p in lib.get("paths", [])]
        return out

    def rebalance_plan(self, libraries: list[str] | None = None) -> dict:
        """预览：把超限库里放不下的目录整体迁到新建序号库。

        libraries 为空 = 所有 DY_ 库（一键整理）；否则只算指定库。"""
        all_libs = self.list_dy_libraries()
        names = [l["name"] for l in all_libs]
        selected = all_libs
        if libraries:
            wanted = {n for n in libraries if n}
            selected = [l for l in all_libs if l["name"] in wanted]
            if not selected:
                raise EmbyError("指定的媒体库不存在或前缀不匹配: "
                                + ", ".join(libraries))
        try:
            emby_counts = self.library_counts(selected)
        except EmbyError as e:
            log.warning("读取媒体库视频数失败，改用本地索引估算: %s", e)
            emby_counts = {}
        counts = _merge_counts(emby_counts)
        folders_by_lib = self.library_folders()
        scope = [l["name"] for l in selected]
        scope_folders = {n: folders_by_lib.get(n, []) for n in scope}
        # 换算比例只用本次范围内的库，否则按单库整理时会拿全站合计去除，比例失真
        ratio = _size_ratio({n: emby_counts.get(n, 0) for n in scope}, scope_folders)
        plan = capacity.plan_rebalance(names, counts,
                                       {n: _scale(rows, ratio)
                                        for n, rows in scope_folders.items()},
                                       _limit())
        plan["counts"] = {n: counts.get(n, 0) for n in scope}
        plan["limit"] = _limit()
        plan["ratio"] = round(ratio, 3)
        plan["scope"] = scope
        return plan

    def rebalance_apply(self, plan: dict | None = None) -> dict:
        """执行整理：把目录迁入计划中的库（已有库直接加路径，没有才新建）。

        实际建库时会重新核对库名，已存在就自动 +1（plan 里的名字只是预排）。"""
        plan = plan or self.rebalance_plan()
        moves = plan.get("moves") or []
        if not moves:
            return {"moves": [], "created": [], "warnings": plan.get("warnings", [])}

        libs = {l["name"]: l for l in self.list_dy_libraries()}
        names = list(libs)
        by_target: dict[str, list[dict]] = {}
        for m in moves:
            by_target.setdefault(m["to"], []).append(m)

        done: list[dict] = []
        created: list[str] = []
        for target, group in by_target.items():
            src = libs.get(group[0]["from"])
            if src is None:
                raise EmbyError(f"源媒体库不存在: {group[0]['from']}")
            paths = [emby_path(m["blogger"]) for m in group]
            if target in libs:
                # 目标是已存在的同族库：先加后删，避免中途失败丢目录
                dst = libs[target]
                for p in paths:
                    self.add_path_to_library(dst["guid"], p, refresh=False)
                actual = target
            else:
                root, _ = capacity.split_family(src["name"], names)
                actual = self.create_library_unique(
                    root, src.get("collection_type", ""), paths,
                    src.get("options"), taken=set(created))
                created.append(actual)
                if actual != target:
                    log.info("整理建库改名: %s -> %s（原名已被占用）", target, actual)
            for m, p in zip(group, paths):
                src_lib = libs.get(m["from"])
                orig = self._find_location(src_lib, m["blogger"]) if src_lib else None
                if orig is None:
                    log.warning("跳过移除：%s 已不在库 %s", m["blogger"], m["from"])
                    continue
                self.remove_path(src_lib, orig)
                db.execute("UPDATE emby_map SET library=? WHERE path=?",
                           (actual, _norm(p)))
                done.append({**m, "to": actual})
        self.refresh_library()
        self.sync_library_paths_to_db()
        log.info("整理媒体库: 新建 %d 个库，迁移 %d 个目录", len(created), len(done))
        return {"moves": done, "created": created,
                "warnings": plan.get("warnings", [])}

    # ---------- 目录映射自检 ----------
    def diagnose_mapping(self) -> dict:
        """三步映射自检 + 库路径反解 + host_video_root 建议（只读）。"""
        from pathlib import Path

        from . import mapping
        from .paths import media_root

        local_root = str(media_root()).replace("\\", "/").rstrip("/")
        subdir = settings.get("media_subdir").strip("/\\")
        current = settings.get("host_video_root").strip()
        prefix = mapping.emby_prefix(current, subdir, local_root)

        libs = self.list_dy_libraries()
        emby_paths = [p for lib in libs for p in lib.get("paths", [])]
        detected = mapping.infer_host_root(emby_paths, subdir)

        indexed = {r["dir_name"].lower(): int(r["video_count"] or 0)
                   for r in db.query("SELECT dir_name, video_count FROM bloggers")}
        libraries = []
        unmapped = unindexed = 0
        for lib in libs:
            rows = []
            for p in lib.get("paths", []):
                blogger = capacity.path_basename(p)
                local = mapping.to_local(p, current, subdir, local_root)
                if not local:
                    unmapped += 1
                if blogger.lower() not in indexed:
                    unindexed += 1
                rows.append({"blogger": blogger, "emby_path": p,
                             "local_path": local or p, "mapped": bool(local),
                             "indexed": blogger.lower() in indexed,
                             "videos": indexed.get(blogger.lower())})
            libraries.append({"name": lib["name"], "dirs": rows})

        mount_ok = Path(local_root).is_dir()
        videos = db.count_videos()
        bloggers = db.count_bloggers()
        issues = []
        if not mount_ok:
            issues.append({"level": "err",
                           "text": f"容器内 {local_root} 不存在或不可读，检查 compose 的 volumes 挂载"})
        if not current and detected["root"]:
            issues.append({"level": "warn",
                           "text": f"没填 Emby 侧根，现在交给 Emby 的路径是容器路径 {local_root}/<博主目录>，"
                                   f"Emby 容器里通常没有这个路径 → 会「加入成功但无法播放」"})
        elif current and detected["root"] and current != detected["root"]:
            issues.append({"level": "warn",
                           "text": f"配置的 Emby 侧根是 {current}，但现有库路径实际指向 "
                                   f"{detected['root']}，两者不一致"})
        if unmapped:
            issues.append({"level": "warn",
                           "text": f"{unmapped} 个库路径无法按当前配置反解回本地目录"})
        if unindexed:
            issues.append({"level": "info",
                           "text": f"{unindexed} 个目录不在本地索引中（可能是在 Emby 里手工添加的）"})
        if not detected["root"]:
            issues.append({"level": "info",
                           "text": "还没有可分析的 DY_ 库路径；先在「浏览」页加入一次，再回来点「重新检测」"})

        steps = [
            {"key": "mount", "title": "① 视频挂载到容器", "ok": mount_ok,
             "text": (f"容器内 {local_root} 可读，已索引 {bloggers} 个博主目录 / {videos} 个视频"
                      if mount_ok else f"容器内 {local_root} 不可读，先去 compose 里加 volumes")},
            {"key": "subdir", "title": "② 绑定子目录（可选）", "ok": True,
             "text": (f"已绑定子目录「{subdir}」，短视频根目录 = {local_root}"
                      if subdir else f"未绑定，博主目录直接放在 {local_root} 根下")},
            {"key": "root", "title": "③ Emby 侧视频根", "ok": bool(current) and (
                not detected["root"] or current == detected["root"]),
             "text": (f"当前 {current}；Emby 里的库路径以此开头" if current
                      else "未填写，Emby 会收到容器路径（一般打不开）")},
        ]

        return {
            "local_root": local_root,
            "media_subdir": subdir,
            "host_video_root": current,
            "emby_prefix": prefix,
            "detected": detected,
            "can_autofix": bool(detected["root"]) and detected["root"] != current,
            "counts": {"libraries": len(libs), "paths": len(emby_paths),
                       "unmapped": unmapped, "unindexed": unindexed,
                       "bloggers": bloggers, "videos": videos},
            "steps": steps,
            "issues": issues,
            "libraries": libraries,
            "formula": f"{prefix or '(未配置)'}/<博主目录>",
        }

    # ---------- 移除 / 同步 ----------
    def remove_blogger_path(self, blogger: str) -> bool:
        """把博主目录从所有含它的 DY_ 媒体库中移除（Emby 端不再显示）。

        只删媒体库里的**路径条目**；Emby 若有残留条目可另行手动清理。
        返回是否发生了移除。Emby 未配置时抛 EmbyError。"""
        removed = False
        for lib in self.list_dy_libraries():
            orig = self._find_location(lib, blogger)
            if orig is None:
                continue
            self.remove_path(lib, orig)
            removed = True
            db.execute("DELETE FROM emby_map WHERE path=? OR path=?",
                       (_norm(orig), _norm(orig).lower()))
            log.info("Emby 移除路径: %s (库 %s)", orig, lib["name"])
        return removed

    def sync_library_paths_to_db(self) -> int:
        """把 Emby 中 DY_ 库的实际路径同步到 emby_map，并刷新 bloggers.in_emby。

        返回映射条数。Emby 侧手工添加的目录也会被登记。"""
        try:
            libs = self.list_dy_libraries()
        except Exception as e:
            raise EmbyError(f"连接 Emby 失败: {e}")
        paths = {_norm(p): lib["name"] for lib in libs for p in lib.get("paths", [])}

        now = time.time()
        # 清掉已不在任何 DY_ 库里的陈旧映射（例如直接在 Emby 侧移除的目录）
        for row in db.query("SELECT path FROM emby_map"):
            if row["path"] not in paths:
                db.execute("DELETE FROM emby_map WHERE path=?", (row["path"],))
        for p, lib_name in paths.items():
            blogger = capacity.path_basename(p)
            db.execute(
                "INSERT INTO emby_map(path, blogger, library, added_at) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET blogger=excluded.blogger, "
                "library=excluded.library",
                (p, blogger, lib_name, now))

        # 博主目录是否已在库中：Emby 路径末段 == 博主目录名，或精确匹配
        local_bloggers = {r["dir_name"]: r["id"] for r in
                          db.query("SELECT id, dir_name FROM bloggers")}
        by_blogger: dict[str, set[str]] = {}
        for p in paths:
            seg = capacity.path_basename(p).lower()
            by_blogger.setdefault(seg, set()).add(p)
        for name, bid in local_bloggers.items():
            emby_paths = by_blogger.get(name.lower(), set())
            if emby_paths:
                db.execute("UPDATE bloggers SET in_emby=1 WHERE id=?", (bid,))
                for p in emby_paths:
                    db.execute(
                        "INSERT INTO emby_map(path, blogger, library, added_at) "
                        "VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                        "blogger=excluded.blogger, library=excluded.library",
                        (p, name, paths[p], now))
            else:
                db.execute("UPDATE bloggers SET in_emby=0 WHERE id=?", (bid,))
        log.info("同步 Emby 库路径: %d 条", len(paths))
        return len(paths)

    def resolve_library(self, library_name: str | None = None,
                        libs: list[dict] | None = None) -> dict:
        """选定要操作的媒体库：由调用方（网页弹窗）手动指定；
        未指定时仅在唯一匹配时自动选取。"""
        libs = libs if libs is not None else self.list_dy_libraries()
        if not libs:
            raise EmbyError(f"未找到 {_prefix()} 开头的媒体库")
        if library_name:
            lib = next((l for l in libs if l["name"] == library_name), None)
            if lib is None:
                raise EmbyError(f"媒体库不存在或前缀不匹配: {library_name}")
            return lib
        if len(libs) > 1:
            raise EmbyError("存在多个 DY_ 媒体库，请手动选择目标库: "
                            + ", ".join(l["name"] for l in libs))
        return libs[0]
