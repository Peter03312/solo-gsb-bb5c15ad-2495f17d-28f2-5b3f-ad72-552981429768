"""资料仓储：论文/评审人的增改删与读取，所有写操作在串行写事务中
完成并令资料修订号自增。"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from . import database as db
from .models import DomainError, PaperIn, ReviewerIn

SEP = "\x1f"


def _split(s: str) -> List[str]:
    return [x for x in s.split(SEP) if x]


def _join(xs: List[str]) -> str:
    return SEP.join(xs)


# ----------------------------- 论文 -----------------------------

def list_papers(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM papers ORDER BY paper_id"))


def get_paper(conn: sqlite3.Connection, paper_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM papers WHERE paper_id=?",
                        (paper_id,)).fetchone()


def create_paper(payload: PaperIn) -> int:
    with db.write_tx() as conn:
        if get_paper(conn, payload.paper_id):
            raise DomainError("duplicate_paper_id",
                              f"论文编号 {payload.paper_id} 已存在", 409)
        rev = db.current_revision(conn)
        conn.execute(
            "INSERT INTO papers(paper_id, anonymous_text, topic, institution,"
            " updated_rev) VALUES (?,?,?,?,?)",
            (payload.paper_id, payload.anonymous_text, payload.topic,
             payload.institution, rev))
        return db.bump_revision(conn)


def update_paper(paper_id: str, payload: PaperIn) -> int:
    with db.write_tx() as conn:
        if not get_paper(conn, paper_id):
            raise DomainError("paper_not_found",
                              f"论文编号 {paper_id} 不存在", 404)
        rev = db.bump_revision(conn)
        conn.execute(
            "UPDATE papers SET anonymous_text=?, topic=?, institution=?,"
            " updated_rev=? WHERE paper_id=?",
            (payload.anonymous_text, payload.topic, payload.institution,
             rev, paper_id))
        return rev


def delete_paper(paper_id: str) -> int:
    with db.write_tx() as conn:
        if not get_paper(conn, paper_id):
            raise DomainError("paper_not_found",
                              f"论文编号 {paper_id} 不存在", 404)
        rev = db.bump_revision(conn)
        conn.execute("DELETE FROM assignments WHERE paper_id=?", (paper_id,))
        # 清理评审人对该论文的回避引用
        rows = conn.execute("SELECT reviewer_id, excluded FROM reviewers").fetchall()
        for r in rows:
            excl = _split(r["excluded"])
            if paper_id in excl:
                conn.execute("UPDATE reviewers SET excluded=? WHERE reviewer_id=?",
                             (_join([x for x in excl if x != paper_id]),
                              r["reviewer_id"]))
        conn.execute("DELETE FROM papers WHERE paper_id=?", (paper_id,))
        return rev


# ----------------------------- 评审人 -----------------------------

def list_reviewers(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM reviewers ORDER BY reviewer_id"))


def get_reviewer(conn: sqlite3.Connection,
                 reviewer_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM reviewers WHERE reviewer_id=?",
                        (reviewer_id,)).fetchone()


def _find_credential(conn: sqlite3.Connection, credential: str,
                     exclude_id: Optional[str] = None
                     ) -> Optional[sqlite3.Row]:
    rows = conn.execute("SELECT reviewer_id, credential FROM reviewers").fetchall()
    for r in rows:
        if exclude_id and r["reviewer_id"] == exclude_id:
            continue
        from .security import verify_secret
        if verify_secret(credential, r["credential"]):
            return r
    return None


def _validate_excluded(conn: sqlite3.Connection, excl: List[str]) -> None:
    dup = {x for x in excl if excl.count(x) > 1}
    if dup:
        raise DomainError("duplicate_excluded_paper",
                          f"回避论文列表存在重复编号：{sorted(dup)}", 400)
    if excl:
        placeholders = ",".join("?" * len(excl))
        found = {r["paper_id"] for r in conn.execute(
            f"SELECT paper_id FROM papers WHERE paper_id IN ({placeholders})",
            excl)}
        missing = sorted(set(excl) - found)
        if missing:
            raise DomainError("excluded_paper_not_found",
                              f"回避论文编号不存在：{missing}", 400)


def create_reviewer(payload: ReviewerIn) -> int:
    with db.write_tx() as conn:
        if get_reviewer(conn, payload.reviewer_id):
            raise DomainError("duplicate_reviewer_id",
                              f"评审人编号 {payload.reviewer_id} 已存在", 409)
        if _find_credential(conn, payload.credential):
            raise DomainError("duplicate_credential",
                              "该凭据已被其他评审人使用，凭据必须唯一", 409)
        _validate_excluded(conn, payload.excluded_papers)
        from .security import hash_secret
        rev = db.current_revision(conn)
        conn.execute(
            "INSERT INTO reviewers(reviewer_id, credential, topics,"
            " institution, capacity, excluded) VALUES (?,?,?,?,?,?)",
            (payload.reviewer_id, hash_secret(payload.credential),
             _join(payload.topics), payload.institution, payload.capacity,
             _join(payload.excluded_papers)))
        return db.bump_revision(conn)


def update_reviewer(reviewer_id: str, payload: ReviewerIn) -> int:
    with db.write_tx() as conn:
        if not get_reviewer(conn, reviewer_id):
            raise DomainError("reviewer_not_found",
                              f"评审人编号 {reviewer_id} 不存在", 404)
        if _find_credential(conn, payload.credential, exclude_id=reviewer_id):
            raise DomainError("duplicate_credential",
                              "该凭据已被其他评审人使用，凭据必须唯一", 409)
        _validate_excluded(conn, payload.excluded_papers)
        from .security import hash_secret
        rev = db.bump_revision(conn)
        conn.execute(
            "UPDATE reviewers SET credential=?, topics=?, institution=?,"
            " capacity=?, excluded=? WHERE reviewer_id=?",
            (hash_secret(payload.credential), _join(payload.topics),
             payload.institution, payload.capacity,
             _join(payload.excluded_papers), reviewer_id))
        return rev


def delete_reviewer(reviewer_id: str) -> int:
    with db.write_tx() as conn:
        if not get_reviewer(conn, reviewer_id):
            raise DomainError("reviewer_not_found",
                              f"评审人编号 {reviewer_id} 不存在", 404)
        rev = db.bump_revision(conn)
        conn.execute("DELETE FROM assignments WHERE reviewer_id=?",
                     (reviewer_id,))
        conn.execute("DELETE FROM reviewers WHERE reviewer_id=?",
                     (reviewer_id,))
        return rev


def authenticate(conn: sqlite3.Connection, reviewer_id: str,
                 credential: str) -> Optional[sqlite3.Row]:
    row = get_reviewer(conn, reviewer_id)
    if row is None:
        return None
    from .security import verify_secret
    if not verify_secret(credential, row["credential"]):
        return None
    return row
