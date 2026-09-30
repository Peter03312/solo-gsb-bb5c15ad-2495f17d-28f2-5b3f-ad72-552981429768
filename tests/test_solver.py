"""求解器正确性测试：用小规模暴力枚举对照
（最小峰值比例 + 字典序最小全局方案）。"""
import itertools
import random

from app.solver import Paper, Reviewer, Solver


def mk_rev(rid, topics, inst, cap, excl=()):
    return Reviewer(rid, frozenset(topics), inst, cap, frozenset(excl))


def brute_force(papers, reviewers):
    """返回 (peak, 扁平序列) 最优解；不可行返回 None。"""
    solver = Solver(papers, reviewers)
    best = None
    for choices in itertools.product(*solver.pairs):
        load = [0] * solver.m
        for (a, b, _em) in choices:
            load[a] += 1
            load[b] += 1
            if load[a] > solver.reviewers[a].capacity or \
               load[b] > solver.reviewers[b].capacity:
                break
        else:
            peak = max((load[i] / solver.reviewers[i].capacity
                        for i in range(solver.m)
                        if solver.reviewers[i].capacity > 0), default=0.0)
            seq = tuple()
            for (a, b, _em) in choices:
                seq += (solver.reviewers[a].reviewer_id,
                        solver.reviewers[b].reviewer_id)
            key = (peak, seq)
            if best is None or key < best:
                best = key
    return best


def solver_key(out, papers, reviewers):
    cap = {r.reviewer_id: r.capacity for r in reviewers}
    load = {}
    seq = tuple()
    by_paper = {}
    for pid, rid in out.assignments:
        load[rid] = load.get(rid, 0) + 1
        by_paper.setdefault(pid, []).append(rid)
    for p in sorted(papers, key=lambda x: x.paper_id):
        a, b = sorted(by_paper[p.paper_id])
        seq += (a, b)
    peak = max((v / cap[k] for k, v in load.items()), default=0.0)
    return peak, seq


def test_matches_bruteforce_random():
    random.seed(42)
    insts = ["I-A", "I-B", "I-C", "I-D"]
    topics = ["ML", "DB", "OS"]
    for trial in range(150):
        np_ = random.randint(1, 4)
        nr = random.randint(2, 5)
        papers = []
        for i in range(np_):
            papers.append(Paper(f"P{i+1}", random.choice(topics),
                                random.choice(insts)))
        reviewers = []
        for i in range(nr):
            rt = random.sample(topics, random.randint(0, 2))
            reviewers.append(mk_rev(
                f"R{i+1}", rt, random.choice(insts),
                random.randint(0, 4),
                frozenset(p.paper_id for p in papers
                          if random.random() < 0.15)))
        out = Solver(papers, reviewers).solve()
        bf = brute_force(papers, reviewers)
        if bf is None:
            assert not out.complete, trial
        else:
            assert out.complete, trial
            assert solver_key(out, papers, reviewers) == bf, trial


def test_min_peak_preferred_over_lex():
    # R1 容量 2，R2 容量 1；两篇论文。
    # 方案必须让 R2 只接 1 篇，峰值比例：R1=1.0, R2=1.0 已是最小。
    papers = [Paper("P1", "ML", "X"), Paper("P2", "ML", "X")]
    reviewers = [mk_rev("R1", ["ML"], "A", 2),
                 mk_rev("R2", ["ML"], "B", 1),
                 mk_rev("R3", ["DB"], "C", 2)]
    # 每篇都需要 ML 专长 => R1/R2 必须出现；R3 补位且与 R1/R2 异机构
    # P1,P2 的组合只可能是 (R1,R2)/(R1,R3 无专长R3? 专长满足因R1)...
    # R1 必须在每篇（R2 容量1不能两篇都上），所以 R2 上一篇、R3 上另一篇。
    out = Solver(papers, reviewers).solve()
    assert out.complete
    assert out.max_load_ratio == 1.0
    # 字典序：P1 取编号最小的可行 (R1,R2)，P2 则 (R1,R3)
    by = {}
    for pid, rid in out.assignments:
        by.setdefault(pid, []).append(rid)
    assert tuple(sorted(by["P1"])) == ("R1", "R2")
    assert tuple(sorted(by["P2"])) == ("R1", "R3")


def test_expertise_and_inst_and_exclusion():
    papers = [Paper("P1", "DB", "I-A")]
    reviewers = [
        mk_rev("R1", ["DB"], "I-A", 5),      # 同机构，排除
        mk_rev("R2", ["ML"], "I-B", 5),      # 不擅长
        mk_rev("R3", ["DB"], "I-B", 5, ["P1"]),  # 回避
        mk_rev("R4", ["DB"], "I-C", 5),
        mk_rev("R5", ["OS"], "I-D", 5),      # 不擅长，但可配 R4
    ]
    out = Solver(papers, reviewers).solve()
    assert out.complete
    rids = {rid for _, rid in out.assignments}
    assert "R4" in rids  # 唯一擅长者必入选
    assert "R1" not in rids


def test_zero_capacity():
    # 唯一擅长者 R1 容量 0：其余 R2/R3 皆不擅长 ML => 无法满足“至少一专家”
    papers = [Paper("P1", "ML", "X")]
    reviewers = [mk_rev("R1", ["ML"], "A", 0),
                 mk_rev("R2", ["DB"], "B", 1),
                 mk_rev("R3", ["OS"], "C", 1)]
    out = Solver(papers, reviewers).solve()
    assert not out.complete
    assert "P1" in out.unassigned


def test_load_balance_prefers_lower_ratio():
    # 三人都擅长、异机构，容量均为 3，三篇论文；最优峰值应为 2/3（每人 2 篇）
    papers = [Paper(f"P{i}", "ML", "X") for i in range(1, 4)]
    reviewers = [mk_rev("R1", ["ML"], "A", 3),
                 mk_rev("R2", ["ML"], "B", 3),
                 mk_rev("R3", ["ML"], "C", 3)]
    out = Solver(papers, reviewers).solve()
    assert out.complete
    assert abs(out.max_load_ratio - 2 / 3) < 1e-9
    # 字典序：P1=(R1,R2), P2 若取 (R1,R2) 会迫使 R3 无位/峰值升高，
    # 最优字典序完整解为 P1=(R1,R2), P2=(R1,R3), P3=(R2,R3)
    by = {}
    for pid, rid in out.assignments:
        by.setdefault(pid, []).append(rid)
    assert tuple(sorted(by["P1"])) == ("R1", "R2")
    assert tuple(sorted(by["P2"])) == ("R1", "R3")
    assert tuple(sorted(by["P3"])) == ("R2", "R3")
