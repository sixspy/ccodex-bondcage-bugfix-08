"""测丙（争用失败测）：双人同入笼锁争用。

口径：两名经办对同一当事人账户同时入笼须抢账户锁；
后到会话只记争用（contention_log 有痕），不死磕成功态，业务零写。
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


def _auth(c: TestClient, user: str) -> dict:
    tok = c.post(
        "/api/login", json={"username": user, "password": "123456"}
    ).json()["token"]
    return {"Authorization": "Bearer " + tok}


def test_late_session_only_records_contention():
    """admin 持账户锁会话未释，clerk 同户入笼：409 + 争用留痕 + 业务零写。"""
    c = TestClient(app)
    h_admin = _auth(c, "admin")
    h_clerk = _auth(c, "clerk")
    assert (
        c.post(
            "/api/bonds",
            headers=h_admin,
            json={"party_key": "P-RACE", "party_name": "测丙户", "initial_deposit": 200000},
        ).status_code
        == 200
    )

    # 会话一（admin）抢先持有该当事人账户锁
    hold = c.post(
        "/api/locks/acquire", headers=h_admin, json={"lock_key": "bond:P-RACE"}
    )
    assert hold.status_code == 200

    # 会话二（clerk）同户入笼：必须 409，不得死磕成功态
    late = c.post(
        "/api/cages/freeze",
        headers=h_clerk,
        json={
            "party_key": "P-RACE",
            "contract_no": "HT-RACE-1",
            "amount": 50000,
            "beneficiary": "受益丙",
            "cage_code": "CG-RACE-001",
        },
    )
    assert late.status_code == 409, "后到会话必须被账户锁挡下"

    # 只记争用：contention_log 必须留痕（业务事务回滚不得带走争用痕）
    cont = c.get("/api/contention", headers=h_admin).json()["items"]
    hits = [x for x in cont if x["lock_key"] == "bond:P-RACE" and x["actor"] == "clerk"]
    assert hits, "后到争用必须留痕 contention_log"

    # 业务零写：笼零增、冻结零改
    detail = c.get("/api/bonds/P-RACE", headers=h_admin).json()
    assert detail["cages"] == []
    assert detail["account"]["frozen"] == 0

    # 会话一释锁后，同户入笼恢复可用
    assert (
        c.post(
            "/api/locks/release", headers=h_admin, json={"lock_key": "bond:P-RACE"}
        ).status_code
        == 200
    )
    again = c.post(
        "/api/cages/freeze",
        headers=h_clerk,
        json={
            "party_key": "P-RACE",
            "contract_no": "HT-RACE-1",
            "amount": 50000,
            "beneficiary": "受益丙",
            "cage_code": "CG-RACE-001",
        },
    )
    assert again.status_code == 200
    assert c.get("/api/bonds/P-RACE", headers=h_admin).json()["account"]["frozen"] == 50000
