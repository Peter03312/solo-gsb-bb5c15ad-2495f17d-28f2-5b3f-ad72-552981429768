"""端到端 HTTP API 测试（FastAPI TestClient）。"""
import base64

import pytest
from fastapi.testclient import TestClient

from app import database as db
from app.main import app

ORG = {"X-Organizer-Key": "test-key"}


@pytest.fixture(scope="module")
def client():
    db.init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(client):
    with db.write_tx() as conn:
        conn.execute("DELETE FROM assignments")
        conn.execute("DELETE FROM reviewers")
        conn.execute("DELETE FROM papers")
        conn.execute("UPDATE meta SET revision=1")
    yield


def basic(rid, cred):
    token = base64.b64encode(f"{rid}:{cred}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def add_paper(client, pid, topic="ML", inst="UniX"):
    r = client.post("/papers", headers=ORG, json={
        "paper_id": pid, "anonymous_text": f"body-{pid}",
        "topic": topic, "institution": inst})
    assert r.status_code == 201, r.text
    return r.json()["material_rev"]


def add_reviewer(client, rid, cred, topics, inst, cap, excl=()):
    r = client.post("/reviewers", headers=ORG, json={
        "reviewer_id": rid, "credential": cred, "topics": topics,
        "institution": inst, "capacity": cap,
        "excluded_papers": list(excl)})
    assert r.status_code == 201, r.text
    return r.json()["material_rev"]


def test_auth_required(client):
    assert client.get("/material").status_code == 401
    assert client.get("/material",
                      headers={"X-Organizer-Key": "wrong"}).status_code == 401


def test_full_flow_dryrun_publish_reviewer_view(client):
    add_paper(client, "P1", "ML", "UniX")
    add_reviewer(client, "R1", "secret-1", ["ML"], "UniA", 2)
    add_reviewer(client, "R2", "secret-2", ["DB"], "UniB", 2)
    add_reviewer(client, "R3", "secret-3", ["ML"], "UniC", 2)

    dr = client.post("/assignments/dry-run", headers=ORG).json()
    assert dr["complete"] is True
    rev = dr["material_rev"]
    # 至少一人擅长；可行二人组 (R1,R2)/(R1,R3) 同峰值，取字典序最小 (R1,R2)
    rids = sorted(a["reviewer_id"] for a in dr["assignments"])
    assert rids == ["R1", "R2"]
    assert sum(a["expert"] for a in dr["assignments"]) >= 1

    # 评审人凭据错误
    assert client.get("/reviewer/me/papers",
                      headers=basic("R1", "nope")).status_code == 401
    # 发布前评审人看不到稿
    r = client.get("/reviewer/me/papers", headers=basic("R3", "secret-3"))
    assert r.json()["papers"] == []

    pr = client.post("/assignments/publish", headers=ORG,
                     json={"material_rev": rev})
    assert pr.status_code == 200, pr.text
    assert pr.json()["material_rev"] == rev

    # 发布后：被分配者可见匿名稿，未被分配者不可见
    r1 = client.get("/reviewer/me/papers", headers=basic("R1", "secret-1")).json()
    assert {p["paper_id"] for p in r1["papers"]} == {"P1"}
    assert r1["papers"][0]["anonymous_text"] == "body-P1"
    r3 = client.get("/reviewer/me/papers", headers=basic("R3", "secret-3")).json()
    assert r3["papers"] == []


def test_publish_rejects_stale_revision(client):
    rev = add_paper(client, "P1", "ML", "UniX")
    add_reviewer(client, "R1", "s1", ["ML"], "UniA", 2)
    add_reviewer(client, "R2", "s2", ["DB"], "UniB", 2)
    add_reviewer(client, "R3", "s3", ["ML"], "UniC", 2)
    # 预演
    dr = client.post("/assignments/dry-run", headers=ORG).json()
    assert dr["complete"]
    # 资料变动 -> 修订号前进
    add_paper(client, "P2", "ML", "UniX")
    r = client.post("/assignments/publish", headers=ORG,
                    json={"material_rev": rev})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "revision_mismatch"
    # 重新预演发布成功
    dr2 = client.post("/assignments/dry-run", headers=ORG).json()
    r = client.post("/assignments/publish", headers=ORG,
                    json={"material_rev": dr2["material_rev"]})
    assert r.status_code == 200


def test_infeasible_dryrun_reasons_and_publish_refused(client):
    add_paper(client, "P1", "ML", "UniA")  # 与 R1 同机构
    add_reviewer(client, "R1", "s1", ["ML"], "UniA", 5)
    add_reviewer(client, "R2", "s2", ["ML"], "UniB", 0)  # 容量 0
    dr = client.post("/assignments/dry-run", headers=ORG).json()
    assert dr["complete"] is False
    assert dr["unassigned"][0]["paper_id"] == "P1"
    assert dr["unassigned"][0]["reasons"]
    r = client.post("/assignments/publish", headers=ORG,
                    json={"material_rev": dr["material_rev"]})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_complete_solution"


def test_invalid_capacity_and_duplicate_ids(client):
    r = client.post("/reviewers", headers=ORG, json={
        "reviewer_id": "R1", "credential": "c", "topics": ["ML"],
        "institution": "A", "capacity": -1})
    assert r.status_code == 422
    add_reviewer(client, "R1", "c", ["ML"], "A", 1)
    r = client.post("/reviewers", headers=ORG, json={
        "reviewer_id": "R1", "credential": "c2", "topics": ["ML"],
        "institution": "B", "capacity": 1})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate_reviewer_id"


def test_duplicate_credential_rejected(client):
    add_reviewer(client, "R1", "same", ["ML"], "A", 1)
    r = client.post("/reviewers", headers=ORG, json={
        "reviewer_id": "R2", "credential": "same", "topics": ["ML"],
        "institution": "B", "capacity": 1})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate_credential"


def test_excluded_paper(client):
    add_paper(client, "P1", "ML", "UniX")
    add_reviewer(client, "R1", "s1", ["ML"], "UniA", 5, excl=["P1"])
    add_reviewer(client, "R2", "s2", ["ML"], "UniB", 5)
    add_reviewer(client, "R3", "s3", ["DB"], "UniC", 5)
    dr = client.post("/assignments/dry-run", headers=ORG).json()
    rids = {a["reviewer_id"] for a in dr["assignments"]}
    assert "R1" not in rids
    assert rids == {"R2", "R3"}


def test_nonexistent_excluded_ref_rejected(client):
    r = client.post("/reviewers", headers=ORG, json={
        "reviewer_id": "R1", "credential": "s", "topics": ["ML"],
        "institution": "A", "capacity": 1, "excluded_papers": ["NOPE"]})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "excluded_paper_not_found"


def test_organizer_sees_assignments_and_reasons(client):
    add_paper(client, "P1", "ML", "UniX")
    add_reviewer(client, "R1", "s1", ["ML"], "UniA", 2)
    add_reviewer(client, "R2", "s2", ["DB"], "UniB", 2)
    add_reviewer(client, "R3", "s3", ["ML"], "UniC", 2)
    dr = client.post("/assignments/dry-run", headers=ORG).json()
    client.post("/assignments/publish", headers=ORG,
                json={"material_rev": dr["material_rev"]})
    view = client.get("/assignments", headers=ORG).json()
    assert len(view["assignments"]) == 2
    assert view["stale"] is False
