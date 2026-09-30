"""预演分配求解器。

约束（每篇论文恰好两名不同评审人）：
  1. 两名评审人来自不同机构，且均不得与作者同机构；
  2. 至少一人擅长该论文主题；
  3. 不得评审本人回避的论文；
  4. 不得超过评审容量（按已分配论文数计）。

择优：
  先最小化“最高已用容量比例” max_i load_i/capacity_i
  （容量为 0 的评审人不可用）；
  再按论文编号顺序、每篇 (评审人编号 a, 评审人编号 b) 的扁平序列，
  取字典序最小的全局方案。

算法（两阶段，均精确）：
  阶段一：阈值 = 各评审人 k/capacity 的离散集合，从小到大用
          MRV+前向检查的回溯判断“是否存在完整方案”，得到最小峰值 T*；
  阶段二：在 T* 下按论文编号顺序逐篇决策，二人组按编号字典序尝试，
          用同一可行性判定器检查“剩余论文是否仍可完成”，第一个通过的
          组合即字典序最优解的该位选择。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

INF = float("inf")
_EPS = 1e-12


@dataclass
class Paper:
    paper_id: str
    topic: str
    institution: str


@dataclass
class Reviewer:
    reviewer_id: str
    topics: frozenset
    institution: str
    capacity: int
    excluded: frozenset


@dataclass
class SolveOutput:
    complete: bool
    max_load_ratio: float
    assignments: List[Tuple[str, str]]  # (paper_id, reviewer_id)
    expert: Dict[Tuple[str, str], bool]
    unassigned: Dict[str, List[str]]
    note: str = ""


class _BudgetExceeded(Exception):
    pass


def _ratio(load: int, capacity: int) -> float:
    return load / capacity if capacity > 0 else INF


class Solver:
    def __init__(self, papers: List[Paper], reviewers: List[Reviewer],
                 node_budget: int = 500_000):
        # 论文按编号排序：字典序口径的规范顺序
        self.papers = sorted(papers, key=lambda p: p.paper_id)
        self.reviewers = sorted(reviewers, key=lambda r: r.reviewer_id)
        self.n = len(self.papers)
        self.m = len(self.reviewers)
        self.node_budget = node_budget

        # pairs[i]：论文 i 的全部可行二人组 (a, b, expert_mask)，
        # 已满足异机构/非同机构/至少一专长；按评审人编号字典序排列
        self.pairs: List[List[Tuple[int, int, int]]] = []
        for p in self.papers:
            plist: List[Tuple[int, int, int]] = []
            ok: List[Tuple[int, bool]] = []
            for ri, r in enumerate(self.reviewers):
                if r.institution == p.institution:
                    continue
                if p.paper_id in r.excluded:
                    continue
                if r.capacity <= 0:
                    continue
                ok.append((ri, p.topic in r.topics))
            for x in range(len(ok)):
                for y in range(x + 1, len(ok)):
                    ai, ea = ok[x]
                    bi, eb = ok[y]
                    ra, rb = self.reviewers[ai], self.reviewers[bi]
                    if ra.institution == rb.institution:
                        continue
                    if not (ea or eb):
                        continue
                    if ra.reviewer_id > rb.reviewer_id:
                        ai, bi, ea, eb = bi, ai, eb, ea
                    em = (1 if ea else 0) | (2 if eb else 0)
                    plist.append((ai, bi, em))
            plist.sort(key=lambda t: (self.reviewers[t[0]].reviewer_id,
                                      self.reviewers[t[1]].reviewer_id))
            self.pairs.append(plist)

        self.nodes = 0

    # ------------------------------------------------------------------
    # 可行性判定：给定初始负载、已固定的论文集合、阈值，剩余能否全分配
    # ------------------------------------------------------------------

    def _feasible(self, load_in: List[int], fixed: frozenset,
                  limit_ratio: float) -> bool:
        """MRV + 前向检查的回溯，只回答“能否完成”，不关心字典序。

        内部使用 load_in 的副本，成功与否都不污染调用方负载状态。
        """
        load = list(load_in)
        cap_room: List[int] = []  # 阈值下各评审人还能承载的论文数
        for r in self.reviewers:
            if r.capacity <= 0:
                cap_room.append(0)
            elif limit_ratio == INF:
                cap_room.append(r.capacity)
            else:
                cap_room.append(int(limit_ratio * r.capacity + _EPS))

        remaining = [i for i in range(self.n) if i not in fixed]

        def opts_for(i: int) -> List[Tuple[int, int, int]]:
            out = []
            for (a, b, em) in self.pairs[i]:
                if load[a] < cap_room[a] and load[b] < cap_room[b]:
                    out.append((a, b, em))
            return out

        def dfs(depth: int) -> bool:
            self.nodes += 1
            if self.nodes > self.node_budget:
                raise _BudgetExceeded()
            if depth == len(remaining):
                return True
            # MRV：选可行组合最少的论文
            best_i_pos = -1
            best_opts: List[Tuple[int, int, int]] = []
            best_len = -1
            for pos in range(depth, len(remaining)):
                i = remaining[pos]
                opts = opts_for(i)
                if not opts:
                    # 动态换位：把死路论文换到 depth 位置即可
                    remaining[depth], remaining[pos] = remaining[pos], remaining[depth]
                    return False
                if best_i_pos == -1 or len(opts) < best_len:
                    best_i_pos, best_len, best_opts = pos, len(opts), opts
            remaining[depth], remaining[best_i_pos] = remaining[best_i_pos], remaining[depth]
            i = remaining[depth]
            for (a, b, _em) in best_opts:
                load[a] += 1
                load[b] += 1
                if dfs(depth + 1):
                    return True
                load[a] -= 1
                load[b] -= 1
            remaining[depth], remaining[best_i_pos] = remaining[best_i_pos], remaining[depth]
            return False

        return dfs(0)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def solve(self) -> SolveOutput:
        if self.n == 0:
            return SolveOutput(True, 0.0, [], {}, note="无论文，空方案")

        infeasible_static = {i for i in range(self.n) if not self.pairs[i]}
        if infeasible_static:
            return self._partial(infeasible_static)

        # 阶段一：离散阈值上二分/遍历求最小可行峰值
        thresholds = {0.0}
        for r in self.reviewers:
            if r.capacity <= 0:
                continue
            for k in range(0, r.capacity + 1):
                thresholds.add(k / r.capacity)
        ordered = sorted(thresholds)

        best_threshold: Optional[float] = None
        try:
            for t in ordered:
                self.nodes = 0
                if self._feasible([0] * self.m, frozenset(), t):
                    best_threshold = t
                    break
        except _BudgetExceeded:
            return self._partial(set(), budget_hit=True)

        if best_threshold is None:  # 容量总量等动态原因导致不可行
            return self._partial(set())

        # 阶段二：规范论文顺序 + 字典序逐位确定，后缀可行性做剪枝
        load = [0] * self.m
        chosen: Dict[int, Tuple[int, int, int]] = {}
        try:
            for i in range(self.n):
                picked: Optional[Tuple[int, int, int]] = None
                for (a, b, em) in self.pairs[i]:
                    ra, rb = self.reviewers[a], self.reviewers[b]
                    if load[a] + 1 > ra.capacity or load[b] + 1 > rb.capacity:
                        continue
                    if _ratio(load[a] + 1, ra.capacity) > best_threshold + _EPS:
                        continue
                    if _ratio(load[b] + 1, rb.capacity) > best_threshold + _EPS:
                        continue
                    load[a] += 1
                    load[b] += 1
                    chosen[i] = (a, b, em)
                    self.nodes = 0
                    ok = self._feasible(load, frozenset(chosen.keys()), best_threshold)
                    load[a] -= 1
                    load[b] -= 1
                    if ok:
                        picked = (a, b, em)
                        break
                    del chosen[i]
                if picked is None:  # 不应发生：阶段一已证明可行
                    return self._partial(set(chosen.keys()))
                a, b, em = picked
                load[a] += 1
                load[b] += 1
                chosen[i] = (a, b, em)
        except _BudgetExceeded:
            return self._partial(set(chosen.keys()), budget_hit=True)

        assignments: List[Tuple[str, str]] = []
        expert: Dict[Tuple[str, str], bool] = {}
        peak = 0.0
        for i in range(self.n):
            a, b, em = chosen[i]
            p = self.papers[i]
            ra, rb = self.reviewers[a], self.reviewers[b]
            assignments.append((p.paper_id, ra.reviewer_id))
            assignments.append((p.paper_id, rb.reviewer_id))
            expert[(p.paper_id, ra.reviewer_id)] = bool(em & 1)
            expert[(p.paper_id, rb.reviewer_id)] = bool(em & 2)
            peak = max(peak, _ratio(load[a], ra.capacity),
                       _ratio(load[b], rb.capacity))
        return SolveOutput(True, peak, assignments, expert, unassigned={})

    # ------------------------------------------------------------------
    # 无完整方案：贪心部分方案 + 逐论文限制原因
    # ------------------------------------------------------------------

    def _partial(self, forced: set, budget_hit: bool = False) -> SolveOutput:
        load = [0] * self.m
        assignments: List[Tuple[str, str]] = []
        expert: Dict[Tuple[str, str], bool] = {}
        unassigned: Dict[str, List[str]] = {}
        peak = 0.0

        for i in range(self.n):
            p = self.papers[i]
            if i in forced:
                unassigned[p.paper_id] = self._reasons(i, load)
                continue
            best: Optional[Tuple[int, int, int]] = None
            best_key: Optional[Tuple[float, str, str]] = None
            for (a, b, em) in self.pairs[i]:
                ra, rb = self.reviewers[a], self.reviewers[b]
                if load[a] + 1 > ra.capacity or load[b] + 1 > rb.capacity:
                    continue
                new_peak = max(peak,
                               _ratio(load[a] + 1, ra.capacity),
                               _ratio(load[b] + 1, rb.capacity))
                key = (new_peak, ra.reviewer_id, rb.reviewer_id)
                if best_key is None or key < best_key:
                    best_key, best = key, (a, b, em)
            if best is None:
                unassigned[p.paper_id] = self._reasons(i, load)
            else:
                a, b, em = best
                ra, rb = self.reviewers[a], self.reviewers[b]
                load[a] += 1
                load[b] += 1
                peak = max(peak, _ratio(load[a], ra.capacity),
                           _ratio(load[b], rb.capacity))
                assignments.append((p.paper_id, ra.reviewer_id))
                assignments.append((p.paper_id, rb.reviewer_id))
                expert[(p.paper_id, ra.reviewer_id)] = bool(em & 1)
                expert[(p.paper_id, rb.reviewer_id)] = bool(em & 2)

        note = "无完整方案：以下论文无法在全部约束下完成双评审分配；已给出的部分方案不会发布。"
        if budget_hit:
            note = ("求解搜索超出预算(SOLVER_NODE_BUDGET)，未能证明完整方案存在，"
                    "返回贪心部分方案；" + note)
        return SolveOutput(complete=not unassigned, max_load_ratio=peak,
                           assignments=assignments, expert=expert,
                           unassigned=unassigned, note=note)

    def _reasons(self, i: int, load: List[int]) -> List[str]:
        p = self.papers[i]
        reasons: List[str] = []

        same = [r.reviewer_id for r in self.reviewers
                if r.institution == p.institution]
        if same:
            reasons.append(f"评审人与作者同机构({p.institution})须回避："
                           + ",".join(same))

        eligible: List[Tuple[int, Reviewer]] = []
        excluded_hit: List[str] = []
        zero_cap: List[str] = []
        for ri, r in enumerate(self.reviewers):
            if r.institution == p.institution:
                continue
            if p.paper_id in r.excluded:
                excluded_hit.append(r.reviewer_id)
                continue
            eligible.append((ri, r))
            if r.capacity <= 0:
                zero_cap.append(r.reviewer_id)
        if excluded_hit:
            reasons.append("评审人已声明回避本论文：" + ",".join(excluded_hit))
        if zero_cap:
            reasons.append("评审人容量为 0：" + ",".join(zero_cap))

        experts = [r for _, r in eligible
                   if p.topic in r.topics and r.capacity > 0]
        if not experts:
            reasons.append(f"无“不同机构、未回避、容量>0”且擅长主题"
                           f"“{p.topic}”的评审人")

        orgs = {r.institution for _, r in eligible if r.capacity > 0}
        if len(orgs) < 2:
            reasons.append("不同机构（未回避且容量>0）的可用评审人不足 2 名，"
                           "无法满足两名异机构评审")
        elif not any(load[a] < self.reviewers[a].capacity
                     and load[b] < self.reviewers[b].capacity
                     for (a, b, _) in self.pairs[i]):
            full = [r.reviewer_id for ri, r in eligible
                    if 0 < r.capacity and load[ri] >= r.capacity]
            reasons.append("可行评审组合受容量限制无法容纳（容量已满："
                           + (",".join(full) if full else "无") + "）")

        if not reasons:
            reasons.append("受机构/回避/主题专长/容量的联合约束，无可行二人组合")
        return reasons
