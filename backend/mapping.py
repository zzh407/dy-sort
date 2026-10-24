"""目录映射：反解 Emby 路径、推断 host_video_root（纯函数，便于单测）。

三个概念（详见首页「指引」页）：

    宿主机磁盘路径  ──(compose volumes)──▶  容器 /data(/绑定子目录)  ──(host_video_root)──▶  Emby 侧路径
    /vol1/.../dy/up                          /data/短视频                     /media/dy/up

本模块只做字符串推导，不碰网络和数据库。
"""
from __future__ import annotations


def split_parent(path: str) -> tuple[str, str]:
    """'/a/b/c/' -> ('/a/b', 'c')；没有父目录时父路径为空。"""
    p = (path or "").replace("\\", "/").rstrip("/")
    if "/" not in p:
        return "", p
    parent, name = p.rsplit("/", 1)
    return parent, name


def join_root(root: str, subdir: str = "") -> str:
    """拼出「Emby 侧根 + 绑定子目录」，即传给 Emby 的路径前缀。"""
    r = (root or "").strip().rstrip("/\\")
    s = (subdir or "").strip("/\\")
    if r and s:
        return f"{r}/{s}"
    return r


def infer_host_root(emby_paths, subdir: str = "") -> dict:
    """从现有库路径推断 host_video_root。

    例：库里有 /media/dy/up/博主A、/media/dy/up/博主B → 返回 /media/dy/up。
    subdir 非空时，要求路径的父目录以 /<subdir> 结尾，推断出的根不含 subdir
    （因为 emby_path() 会再拼一次）。
    """
    sub = (subdir or "").strip("/\\")
    votes: dict[str, int] = {}
    for raw in emby_paths or []:
        parent, _ = split_parent(raw)
        if not parent:
            continue
        root = parent
        if sub:
            if parent == sub or parent.endswith("/" + sub):
                root = parent[: len(parent) - len(sub)].rstrip("/")
            else:
                continue
        if not root:
            continue
        votes[root] = votes.get(root, 0) + 1
    if not votes:
        return {"root": "", "votes": 0, "total": len(emby_paths or []), "samples": []}
    root = max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]
    prefix = join_root(root, sub)
    samples = [p for p in emby_paths
               if (p or "").replace("\\", "/").startswith(prefix + "/")][:3]
    return {"root": root, "votes": votes[root], "total": len(emby_paths or []),
            "samples": samples}


def to_local(emby_path: str, host_root: str, subdir: str,
             local_root: str) -> str:
    """Emby 侧路径 -> 容器内本地路径；前缀对不上时返回空串。"""
    base = join_root(host_root, subdir)
    p = (emby_path or "").replace("\\", "/").rstrip("/")
    local = (local_root or "").replace("\\", "/").rstrip("/")
    if not base or not p.lower().startswith(base.lower()):
        return ""
    tail = p[len(base):]
    return local + tail


def emby_prefix(host_root: str, subdir: str, local_root: str) -> str:
    """当前配置下「本地根」应对应的 Emby 侧前缀（用于展示与一致性比较）。"""
    base = join_root(host_root, subdir)
    return base or (local_root or "").replace("\\", "/").rstrip("/")
