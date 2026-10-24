"""媒体库容量规划（纯函数，无网络/数据库 IO，便于单测）。

核心约束
--------
1. **目录是拆分的最小单位**：同一个博主目录只会整体属于一个媒体库，
   任何情况下都不把目录里的视频分到两个库。
2. 每个库的视频数受 ``emby_library_max_videos`` 限制（默认 2000）。
3. 超限时优先填**同族已有空位的序号库**（``DY_瑜伽裤2``），都没有空位才新建
   ``DY_瑜伽裤3``；序号只增不减，已存在的序号不会被复用。

命名解析
--------
只有当去掉尾部数字后的名字本身是一个已存在的库时，才把它当作序号成员。
所以 ``DY_丝袜3`` 属于家族 ``DY_丝袜``；而 ``DY_2024`` 不会被误认成 ``DY_2`` 家族的成员。
"""
from __future__ import annotations

import re

_SUFFIX = re.compile(r"^(?P<root>.+?)(?P<num>\d+)$")


def path_basename(path: str) -> str:
    """Emby 路径 -> 末段目录名（兼容 Windows 反斜杠）。"""
    return (path or "").replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def split_family(name: str, names) -> tuple[str, int]:
    """库名 -> (家族根名, 序号)；根库序号为 1。"""
    m = _SUFFIX.match(name or "")
    if m:
        root, num = m.group("root"), int(m.group("num"))
        if num >= 2 and root in names:
            return root, num
    return name, 1


def family_members(root: str, names) -> dict[str, int]:
    """家族根名 -> {库名: 序号}。"""
    out = {}
    for name in names:
        r, seq = split_family(name, names)
        if r == root:
            out[name] = seq
    return out


def next_name(root: str, names) -> str:
    """家族里下一个可用库名（不重复使用已有序号）。"""
    seqs = [seq for r, seq in (split_family(n, names) for n in names) if r == root]
    return f"{root}{max(seqs + [1]) + 1}"


def free_members(root: str, names, room: dict, skip=()) -> list[tuple[int, str, int]]:
    """同族里还有空位的库，按序号升序返回 ``[(序号, 库名, 剩余)]``。

    ``room`` 是各库剩余容量（会被调用方边放边扣）。"""
    out = []
    for name, seq in family_members(root, names).items():
        if name in skip:
            continue
        left = int(room.get(name, 0) or 0)
        if left > 0:
            out.append((seq, name, left))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def plan_add(target: str, names, counts: dict, folders, limit: int) -> dict:
    """把待加入的目录分配到目标库、同族有空位的库，最后才新建序号库。

    参数
    ----
    target   用户选定的目标库名（优先填它）
    names    现有 DY_ 库名列表
    counts   {库名: 当前视频数}（Emby 实测）
    folders  [(目录名, 视频数)]，顺序无关；内部按体积降序装箱
    limit    每个库的视频上限（>=1）

    返回 ``{"root", "assignments", "created", "warnings"}``：
    assignments 每项 ``{"library", "created", "bloggers", "videos", "before"}``。
    """
    limit = max(1, int(limit or 1))
    root, _ = split_family(target, names)
    known = list(names)
    created: list[str] = []
    warnings: list[str] = []
    groups: list[dict] = []
    room = {n: max(0, limit - int(counts.get(n, 0) or 0)) for n in names}
    current = target

    def group_for(lib: str) -> dict:
        for g in groups:
            if g["library"] == lib:
                return g
        g = {"library": lib, "created": lib in created, "bloggers": [], "videos": 0,
             "before": 0 if lib in created else int(counts.get(lib, 0) or 0)}
        groups.append(g)
        return g

    def pick(v: int) -> str:
        """先填当前库，再填同族其它有空位的库（序号升序）；都没有返回空串。"""
        if room.get(current, 0) >= v:
            return current
        for _seq, name, left in free_members(root, known, room, skip=(current,)):
            if left >= v:
                return name
        return ""

    for folder, videos in sorted(folders, key=lambda x: (-(x[1] or 0), x[0])):
        v = max(0, int(videos or 0))
        if v > limit:
            # 单个目录就超过上限：独占一个新库，仍不拆分目录；
            # 这个库装满「一个目录」即止，后续目录继续按空位分配。
            new = next_name(root, known)
            created.append(new)
            known.append(new)
            room[new] = 0
            g = group_for(new)
            g["bloggers"].append(folder)
            g["videos"] += v
            warnings.append(f"「{folder}」自身 {v} 个视频超过单库上限 {limit}，"
                            f"已单独建库「{new}」（目录不可拆分）")
            continue
        lib = pick(v)
        if not lib:
            lib = next_name(root, known)
            created.append(lib)
            known.append(lib)
            room[lib] = limit
        g = group_for(lib)
        g["bloggers"].append(folder)
        g["videos"] += v
        room[lib] -= v
        current = lib

    return {"root": root, "assignments": groups, "created": created, "warnings": warnings}


def plan_rebalance(names, counts: dict, folders_by_lib: dict, limit: int) -> dict:
    """把超限库里「放不下」的目录整体迁到同族有空位的库，没有才新建序号库。

    folders_by_lib: ``{库名: [(目录名, 视频数)]}``（来自 Emby 实际路径）
    返回 ``{"moves", "created", "libraries", "warnings"}``：
    moves 每项 ``{"blogger", "videos", "from", "to"}``。
    """
    limit = max(1, int(limit or 1))
    moves: list[dict] = []
    created: list[str] = []
    libs: list[dict] = []
    warnings: list[str] = []
    known = list(names)
    # 各库剩余容量；边迁边扣，已超限的库剩余为 0，不会被当作目标
    room = {n: max(0, limit - int(counts.get(n, 0) or 0)) for n in names}

    over_libs = [n for n in folders_by_lib if int(counts.get(n, 0) or 0) > limit]
    for lib in sorted(over_libs, key=lambda n: (-int(counts.get(n, 0) or 0), n)):
        before = int(counts.get(lib, 0) or 0)
        root, _ = split_family(lib, names)
        folders = [(b, max(0, int(v or 0))) for b, v in folders_by_lib.get(lib, [])]
        need = before - limit

        # 大目录优先：用最少的迁移次数把库降到上限以内。
        # 单个目录本身超过上限的也可以迁出（独占一个新库），否则永远无法把原库降下来；
        # 它所在的新库必然超限，单独给出告警。
        movable = sorted((f for f in folders if f[1] > 0), key=lambda x: (-x[1], x[0]))
        picks: list[tuple[str, int]] = []
        picked_videos = 0
        for folder, v in movable:
            if picked_videos >= need:
                break
            picks.append((folder, v))
            picked_videos += v

        if not picks:
            warnings.append(f"「{lib}」共 {before} 个视频，超限 {need} 个，但没有可迁移的目录")
            continue

        for folder, v in picks:          # picks 已按体积降序
            if v > limit:
                cur = next_name(root, known)
                created.append(cur)
                known.append(cur)
                room[cur] = 0
                moves.append({"blogger": folder, "videos": v, "from": lib, "to": cur})
                warnings.append(f"「{folder}」自身 {v} 个视频超过上限 {limit}，"
                                f"整体迁到新库「{cur}」后该库仍会超限（目录不可拆）")
                continue
            # 先填同族还有空位的库（序号升序，排除源库本身）；都没有才新建
            target = ""
            for _seq, name, left in free_members(root, known, room, skip=(lib,)):
                if left >= v:
                    target = name
                    break
            if not target:
                target = next_name(root, known)
                created.append(target)
                known.append(target)
                room[target] = limit
            moves.append({"blogger": folder, "videos": v, "from": lib, "to": target})
            room[target] -= v

        after = max(0, before - picked_videos)
        note = ""
        if after > limit:
            note = f"，迁出后仍 {after} 个"
            warnings.append(f"「{lib}」迁出 {picked_videos} 个视频后仍剩 {after} 个，"
                            f"超过上限 {limit}（剩余目录体积之和仍过大）")
        libs.append({"library": lib, "root": root, "before": before,
                     "moved": picked_videos, "after": after, "note": note})

    return {"moves": moves, "created": created, "libraries": libs, "warnings": warnings}
