"""SQLite 数据层：视频索引与 Emby 映射。

（运行配置在 config/config.yaml，见 settings.py。）
WAL 模式 + 单连接 + 可重入线程锁，适合本项目的读多写少场景。"""
from __future__ import annotations

import sqlite3
import threading

DB_PATH = "/app/.cache/dy-sort.db"

_conn: sqlite3.Connection | None = None
# 可重入锁：connect() 内部也取同一把锁，普通 Lock 会自死锁
_lock = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bloggers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dir_name    TEXT UNIQUE NOT NULL,          -- 一级目录名（博主ID）
    metadata    TEXT NOT NULL DEFAULT '{}',    -- 元数据 JSON
    poster      TEXT,                          -- 博主海报绝对路径（容器内）
    poster_key  TEXT,                          -- 博主海报 key（流接口用）
    video_count INTEGER NOT NULL DEFAULT 0,
    in_emby     INTEGER NOT NULL DEFAULT 0,    -- 博主目录是否已在 Emby
    first_seen  REAL NOT NULL DEFAULT 0,
    last_seen   REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS videos (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    blogger_id INTEGER NOT NULL REFERENCES bloggers(id) ON DELETE CASCADE,
    rel        TEXT UNIQUE NOT NULL,          -- 博主ID/视频名/文件名
    name       TEXT NOT NULL,                 -- 二级目录名（视频名）
    file_name  TEXT NOT NULL,
    size       INTEGER NOT NULL DEFAULT 0,
    poster     TEXT,                          -- 海报绝对路径（容器内）
    poster_key TEXT,
    metadata   TEXT NOT NULL DEFAULT '{}',    -- 视频级元数据 JSON
    duration   REAL,
    width      INTEGER,
    height     INTEGER,
    first_seen REAL NOT NULL DEFAULT 0,
    last_seen  REAL NOT NULL DEFAULT 0,
    UNIQUE(blogger_id, name)
);

CREATE INDEX IF NOT EXISTS idx_videos_blogger ON videos(blogger_id);
CREATE INDEX IF NOT EXISTS idx_videos_name ON videos(name);

CREATE TABLE IF NOT EXISTS emby_map (
    path     TEXT PRIMARY KEY,    -- Emby 侧路径（归一化）
    blogger  TEXT NOT NULL,
    library  TEXT,
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS blogger_style (
    blogger    TEXT PRIMARY KEY,               -- 博主目录名
    style      TEXT NOT NULL DEFAULT '',       -- 主风格标签（= labels 里数量最多的）
    labels     TEXT NOT NULL DEFAULT '[]',     -- 风格多标签明细 JSON：[{style,count,ratio}]
    body       TEXT NOT NULL DEFAULT '',       -- 主身材标签（= body_labels 里数量最多的）
    body_labels TEXT NOT NULL DEFAULT '[]',    -- 身材多标签明细 JSON
    source     TEXT NOT NULL DEFAULT '',       -- manual | vision | library | name
    confidence REAL NOT NULL DEFAULT 0,
    note       TEXT NOT NULL DEFAULT '',       -- 识别备注（采样数、模型理由等）
    updated_at REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_style ON blogger_style(style);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """老库补列：labels（多标签明细）、body / body_labels（身材维度）。"""
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(blogger_style)")}
    except sqlite3.Error:
        return
    if not cols:
        return
    if "labels" not in cols:
        conn.execute("ALTER TABLE blogger_style "
                     "ADD COLUMN labels TEXT NOT NULL DEFAULT '[]'")
    if "body" not in cols:
        conn.execute("ALTER TABLE blogger_style "
                     "ADD COLUMN body TEXT NOT NULL DEFAULT ''")
    if "body_labels" not in cols:
        conn.execute("ALTER TABLE blogger_style "
                     "ADD COLUMN body_labels TEXT NOT NULL DEFAULT '[]'")


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA foreign_keys=ON")
            _conn.execute("PRAGMA busy_timeout=5000")
            _conn.executescript(_SCHEMA)
            _migrate(_conn)
            _conn.commit()
        return _conn


def query(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return connect().execute(sql, args).fetchall()


def query_one(sql: str, args: tuple = ()) -> sqlite3.Row | None:
    with _lock:
        return connect().execute(sql, args).fetchone()


def execute(sql: str, args: tuple = (), commit: bool = True):
    """执行 SQL，返回 cursor（可用 .lastrowid / .rowcount）。

    commit=False 时不立即提交，适合扫描等批量写入场景，
    由调用方在批末调用 commit() 统一提交。"""
    with _lock:
        cur = connect().execute(sql, args)
        if commit:
            _conn.commit()
        return cur


def commit() -> None:
    with _lock:
        if _conn is not None:
            _conn.commit()


def executemany(sql: str, rows: list[tuple]) -> None:
    with _lock:
        _conn.executemany(sql, rows)
        _conn.commit()


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


# ---------- 索引维护 ----------
def reset_index() -> None:
    """清空索引（换绑短视频根目录后旧索引失效）。"""
    execute("DELETE FROM videos")
    execute("DELETE FROM bloggers")
    execute("DELETE FROM emby_map")


# ---------- 统计 ----------
def count_videos() -> int:
    row = query_one("SELECT COUNT(*) c FROM videos")
    return row["c"] if row else 0


def count_bloggers() -> int:
    row = query_one("SELECT COUNT(*) c FROM bloggers")
    return row["c"] if row else 0
