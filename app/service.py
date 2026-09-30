"""业务服务层：装配求解器输入、预演、发布与查询。"""
from __future__ import annotations

import sqlite3
from typing import List, Tuple

from . import database as db, repository as repo
from .config import settings
from .models import (Assignment, DryRunResult, PublishResult,
                     UnassignedPaper)
from .solver import Paper, Reviewer, Solver, SolveOutput


def _load_material(conn: sqlite3.Connection
                   ) -> Tuple[List[Paper], List[Reviewer], dict, dict]:
    prows = repo.list_papers(conn)
    rrows = repo.list_reviewers(conn)
    papers = [Paper(p["paper_id"], p["topic"], p["institution"])
              for p in prows]
    reviewers = [Reviewer(
        r["reviewer_id"], frozenset(repo._split(r["topics"])),
        r["institution"], int(r["capacity"]),
        frozenset(repo._split(r["excluded"]))) for r in rrows]
    pmap = {p["paper_id"]: dict(p) for p in prows}
    rmap = {r["reviewer_id"]: dict(r) for r in rrows}
    return papers, reviewers, pmap, rmap


def _to_result(out: SolveOutput, rev: int) -> DryRunResult:
    assignments = [
        Assignment(paper_id=pid, reviewer_id=rid,
                   expert=out.expert[(pid, rid)])
        for (pid, rid) in out.assignments
    ]
    unassigned = [
        UnassignedPaper(paper_id=pid, reasons=reasons)
        for pid, reasons in sorted(out.unassigned.items())
    ]
    return DryRunResult(
        material_rev=rev,
        complete=out.complete,
        max_load_ratio=round(out.max_load_ratio, 6),
        assignments=assignments,
        unassigned=unassigned,
        note=out.note,
    )


def dry_run() -> DryRunResult:
    """预演：不落库，返回当前资料修订号下的最优（或部分）方案。"""
    with db.get_conn() as conn:
        rev = db.current_revision(conn)
        papers, reviewers, _, _ = _load_material(conn)
    solver = Solver(papers, reviewers, settings.solver_node_budget)
    return _to_result(solver.solve(), rev)


def publish(material_rev: int) -> PublishResult:
    """发布预演方案。

    在单个串行写事务内：
      校验 revision == 当前修订号 -> 用同一快照求解 -> 必须完整 ->
      清空旧方案并写入新方案（记录 material_rev 与发布时修订号）。
    资料在预演与发布之间发生任何变动，revision 即不匹配并拒绝发布，
    因此并发的资料修改不可能让旧版方案被发布。
    """
    with db.write_tx() as conn:
        current = db.current_revision(conn)
        if material_rev != current:
            from .models import DomainError
            raise DomainError(
                "revision_mismatch",
                f"资料已变动（预演修订号={material_rev}，当前修订号={current}），"
                "请基于最新资料重新预演后再发布",
                409,
                {"dry_run_rev": material_rev, "current_rev": current})

        papers, reviewers, _, _ = _load_material(conn)
        solver = Solver(papers, reviewers, settings.solver_node_budget)
        out = solver.solve()
        if not out.complete:
            from .models import DomainError
            raise DomainError(
                "no_complete_solution",
                "当前资料不存在完整双评审方案，拒绝发布；"
                "请查看预演接口返回的未分配论文及限制原因",
                422,
                {"unassigned": [
                    {"paper_id": pid, "reasons": reasons}
                    for pid, reasons in sorted(out.unassigned.items())]})

        conn.execute("DELETE FROM assignments")
        for (pid, rid) in out.assignments:
            conn.execute(
                "INSERT INTO assignments(paper_id, reviewer_id,"
                " material_rev, published_rev) VALUES (?,?,?,?)",
                (pid, rid, current, current))
        assignments = [
            Assignment(paper_id=pid, reviewer_id=rid,
                       expert=out.expert[(pid, rid)])
            for (pid, rid) in out.assignments]
        return PublishResult(
            published=True, material_rev=current,
            assignments=assignments,
            note=f"已发布基于资料修订号 {current} 的方案")


def published_snapshot() -> dict:
    """会务方查看：当前发布版方案 + 资料修订号；以及实时预演的排除原因。"""
    with db.get_conn() as conn:
        rev = db.current_revision(conn)
        rows = conn.execute(
            "SELECT paper_id, reviewer_id, material_rev FROM assignments"
            " ORDER BY paper_id, reviewer_id").fetchall()
        published_rev = rows[0]["material_rev"] if rows else None
        stale = published_rev is not None and published_rev != rev

        papers, reviewers, pmap, _ = _load_material(conn)
        solver = Solver(papers, reviewers, settings.solver_node_budget)
        live = solver.solve()
        live_unassigned = [
            {"paper_id": pid, "reasons": reasons}
            for pid, reasons in sorted(live.unassigned.items())]

        # 已发布但当前资料下不再可行的条目（资料变动导致）
        live_pairs = set(live.assignments)
        invalidated = [
            {"paper_id": r["paper_id"], "reviewer_id": r["reviewer_id"],
             "reason": "资料变动后该条分配不再属于当前可行方案"}
            for r in rows
            if (r["paper_id"], r["reviewer_id"]) not in live_pairs]

    return {
        "material_rev_at_publish": published_rev,
        "current_material_rev": rev,
        "stale": stale,
        "assignments": [
            {"paper_id": r["paper_id"], "reviewer_id": r["reviewer_id"]}
            for r in rows],
        "current_unassigned_and_reasons": live_unassigned,
        "invalidated_after_material_change": invalidated,
        "note": ("发布版保持不变；资料已变动，发布版为历史版本" if stale
                 else "发布版与当前资料一致"),
    }


def reviewer_view(reviewer_id: str, credential: str) -> dict:
    """评审人凭自身凭据仅读取已分配给自己的匿名稿。"""
    with db.get_conn() as conn:
        row = repo.authenticate(conn, reviewer_id, credential)
        if row is None:
            from .models import DomainError
            raise DomainError("invalid_credential",
                              "评审人编号或凭据无效", 401)
        assigned = conn.execute(
            """SELECT a.paper_id, a.material_rev, p.topic, p.anonymous_text
               FROM assignments a JOIN papers p ON p.paper_id = a.paper_id
               WHERE a.reviewer_id = ?
               ORDER BY a.paper_id""",
            (reviewer_id,)).fetchall()
        topics = frozenset(repo._split(row["topics"]))
    return {
        "reviewer_id": reviewer_id,
        "material_rev": assigned[0]["material_rev"] if assigned else None,
        "papers": [{
            "paper_id": r["paper_id"],
            "topic": r["topic"],
            "anonymous_text": r["anonymous_text"],
            "expert": r["topic"] in topics,
        } for r in assigned],
    }
