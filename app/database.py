"""SQLite 访问层。

修订号(revision)为资料的逻辑版本：论文/评审人的任何录入、修改、删除
都会让 revision 自增。发布必须携带预演时所见的 revision，资料一旦变动，
旧 revision 的发布即被拒绝，从而杜绝并发下发布旧版方案。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

from .config import settings

# 写操作串行化：SQLite 单库 + 显式事务，应用级锁保证
# “检查 revision → 写入发布”这类复合操作的原子性。
_write_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL DEFAULT 1
);

INSERT OR IGNORE INTO meta(id, revision) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS papers (
    paper_id        TEXT PRIMARY KEY,
    anonymous_text TEXT NOT NULL,
    topic           TEXT NOT NULL,
    institution     TEXT NOT NULL,
    updated_rev     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reviewers (
    reviewer_id   TEXT PRIMARY KEY,
    credential    TEXT NOT NULL,
    topics        TEXT NOT NULL,           -- 以 \x1f 分隔的主题列表
    institution   TEXT NOT NULL,
    capacity      INTEGER NOT NULL CHECK (capacity >= 0),
    excluded      TEXT NOT NULL DEFAULT '' -- 以 \x1f 分隔的回避论文编号
);

CREATE TABLE IF NOT EXISTS assignments (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id         TEXT NOT NULL,
    reviewer_id      TEXT NOT NULL,
    material_rev     INTEGER NOT NULL,     -- 本方案基于的资料修订号
    published_rev    INTEGER NOT NULL,     -- 发布动作发生时的资料修订号
    UNIQUE(paper_id, reviewer_id)
);

CREATE INDEX IF NOT EXISTS idx_assign_reviewer ON assignments(reviewer_id);
"""


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(settings.db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    """创建表并初始化修订号；容器启动时调用一次。"""
    conn = _connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_tx() -> Iterator[sqlite3.Connection]:
    """串行化的写事务。复合操作（改资料 + bump revision）在此完成。"""
    with _write_lock:
        conn = _connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def current_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT revision FROM meta WHERE id=1").fetchone()
    return int(row["revision"])


def bump_revision(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE meta SET revision = revision + 1 WHERE id=1")
    return current_revision(conn)
