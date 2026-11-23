"""复现脚本：入笼成功但冻结不抬、互证假绿、旧冻额下超额再入。

仅打本机文件库（data/app.db）上运行的服务，不碰 pytest 临时库。
用法：先起服务（uvicorn），再 python scripts/repro_incident.py [base_url]
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11333"
PARTY = "P-WEST"  # 种子户：余额 150000 / 冻结 0，可再入超额种子
AMOUNT = 120000   # 首次入笼后真实可用仅剩 30000，第二笔同额须被拒


def req(method: str, path: str, token: str | None = None, body: dict | None = None):
    r = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main() -> int:
    _, login = req("POST", "/api/login", body={"username": "admin", "password": "123456"})
    tok = login["token"]

    _, bonds = req("GET", "/api/bonds", tok)
    before = next(b for b in bonds["items"] if b["party_key"] == PARTY)
    print(f"[0] 入笼前 {PARTY}: balance={before['balance']} frozen={before['frozen']}")

    s1, cage1 = req("POST", "/api/cages/freeze", tok, {
        "party_key": PARTY, "contract_no": "HT-REPRO-1", "amount": AMOUNT,
        "beneficiary": "复现受益人", "cage_code": "CG-REPRO-001",
    })
    print(f"[1] 第一笔入笼 {AMOUNT}: HTTP {s1} cage={cage1.get('cage_code')} status={cage1.get('status')}")

    _, acct = req("GET", f"/api/bonds/{PARTY}", tok)
    a = acct["account"]
    print(f"[2] 入笼后账户: balance={a['balance']} frozen={a['frozen']}  "
          f"{'<<< 事故：冻结未抬' if a['frozen'] == before['frozen'] else '冻结已抬'}")

    _, cages = req("GET", "/api/cages", tok)
    visible = [c["cage_code"] for c in cages["items"] if c["party_key"] == PARTY]
    print(f"[3] 名册可见笼: {visible}  <<< 新笼已亮" if "CG-REPRO-001" in visible else f"[3] 名册: {visible}")

    _, proof = req("GET", f"/api/proof/{PARTY}", tok)
    fake_green = proof["frozen_matches_live"] and proof["proof"]["left"] != proof["proof"]["right"]
    print(f"[4] 互证: frozen={proof['proof']['left']} live={proof['proof']['right']} "
          f"matches={proof['frozen_matches_live']}  {'<<< 假绿' if fake_green else ''}")

    s2, cage2 = req("POST", "/api/cages/freeze", tok, {
        "party_key": PARTY, "contract_no": "HT-REPRO-2", "amount": AMOUNT,
        "beneficiary": "复现受益人", "cage_code": "CG-REPRO-002",
    })
    over = s2 == 200
    print(f"[5] 旧冻额下再押 {AMOUNT}（真实可用仅 {before['balance'] - before['frozen'] - AMOUNT}）: "
          f"HTTP {s2}  {'<<< 事故：超额再入成功' if over else '已被拒(符合预期)'}")

    _, a2 = req("GET", f"/api/bonds/{PARTY}", tok)
    print(f"[6] 终态账户: frozen={a2['account']['frozen']}")

    incident = (a["frozen"] == before["frozen"]) or fake_green or over
    print("RESULT:", "事故复现：冻不加/假绿/超额再入" if incident else "未见事故（已修复？）")
    return 0 if incident else 1


if __name__ == "__main__":
    raise SystemExit(main())
