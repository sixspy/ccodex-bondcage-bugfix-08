"""测乙（业务失败测）：旧冻额下禁止再入。

口径：冻结已同事务抬高后，可入笼额 = 余额 − 已冻结；
超额再入必须 409，且账户与名册零改，只留拒写审计痕。
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


def test_overlimit_reentry_rejected():
    """首笔入笼 120000 后真实可用仅 30000，第二笔同额必须被拒。"""
    c = TestClient(app)
    h = _auth(c)
    assert (
        c.post(
            "/api/bonds",
            headers=h,
            json={"party_key": "P-OVER", "party_name": "测乙户", "initial_deposit": 150000},
        ).status_code
        == 200
    )

    first = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": "P-OVER",
            "contract_no": "HT-OVER-1",
            "amount": 120000,
            "beneficiary": "受益乙",
            "cage_code": "CG-OVER-001",
        },
    )
    assert first.status_code == 200

    second = c.post(
        "/api/cages/freeze",
        headers=h,
        json={
            "party_key": "P-OVER",
            "contract_no": "HT-OVER-2",
            "amount": 120000,
            "beneficiary": "受益乙",
            "cage_code": "CG-OVER-002",
        },
    )
    assert second.status_code == 409, "旧冻额下超额再入必须 409"

    detail = c.get("/api/bonds/P-OVER", headers=h).json()
    assert detail["account"]["frozen"] == 120000, "拒写后冻结不得再动"
    assert len(detail["cages"]) == 1, "拒写后新笼可见行必须零增"

    audits = c.get("/api/audit?limit=200", headers=h).json()["items"]
    assert any(
        a["action"] == "cage_freeze_reject" and a["target"] == "CG-OVER-002"
        for a in audits
    ), "超额拒写必须只留 cage_freeze_reject 审计痕"

    proof = c.get("/api/proof/P-OVER", headers=h).json()
    assert proof["frozen_matches_live"] is True
