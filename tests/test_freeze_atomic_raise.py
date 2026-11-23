"""测甲（业务失败测）：插笼抬冻原子性。

口径（审锁纪要 2026-11-23 封存，甲路径）：
新笼名册写入成功，则账户冻结必同事务抬高；任一侧失败整笔回滚，正式零改。
"""
import pytest
from fastapi.testclient import TestClient

from app import app
from db import init_db


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr("db.DB_PATH", tmp_path / "t.db")
    init_db()
    yield


def _auth(c: TestClient, user="admin") -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def _open(c, h, key, amount):
    r = c.post(
        "/api/bonds",
        headers=h,
        json={"party_key": key, "party_name": "测甲户", "initial_deposit": amount},
    )
    assert r.status_code == 200


def test_freeze_raises_frozen_atomically():
    """新笼落册成功 ⇒ 账户冻结同原子抬高，余额不动，互证真绿。"""
    c = TestClient(app)
    h = _auth(c)
    _open(c, h, "P-ATOM", 100000)

    r = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": "P-ATOM",
            "contract_no": "HT-ATOM-1",
            "amount": 40000,
            "beneficiary": "受益甲",
            "cage_code": "CG-ATOM-001",
        },
    )
    assert r.status_code == 200

    acct = c.get("/api/bonds/P-ATOM", headers=h).json()["account"]
    assert acct["frozen"] == 40000, "入笼成功后账户冻结必须同事务抬高"
    assert acct["balance"] == 100000, "入笼只动冻结，余额不得动"

    proof = c.get("/api/proof/P-ATOM", headers=h).json()
    assert proof["frozen_matches_live"] is True
    assert proof["proof"]["left"] == proof["proof"]["right"] == 40000


def test_failed_freeze_rolls_back_everything():
    """入笼被拒 ⇒ 整笔回滚：笼零增、冻结零改，只留拒写审计痕。"""
    c = TestClient(app)
    h = _auth(c)
    _open(c, h, "P-ATOM2", 50000)

    r = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": "P-ATOM2",
            "contract_no": "HT-ATOM-2",
            "amount": 999999,
            "beneficiary": "受益甲",
            "cage_code": "CG-ATOM-002",
        },
    )
    assert r.status_code == 409

    detail = c.get("/api/bonds/P-ATOM2", headers=h).json()
    assert detail["account"]["frozen"] == 0, "拒写后冻结必须零改"
    assert detail["cages"] == [], "拒写后新笼可见行必须零增"

    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    rejects = [
        a for a in audits
        if a["action"] == "cage_freeze_reject" and a["target"] == "CG-ATOM-002"
    ]
    assert rejects, "拒写必须只留 cage_freeze_reject 审计痕"
