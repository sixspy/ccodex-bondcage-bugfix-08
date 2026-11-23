"""五页账实核对：入笼页 / 账户页 / 对账页 / 审计页 / 保证金台账。

在干净种子库上走一笔入笼（入笼页表单默认值：P-WEST 30000），
逐页拉取页面数据源，核对新笼与冻结同增、互证真绿、审计留痕。
用法：python scripts/verify_pages.py [base_url]
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11333"
PARTY, AMOUNT = "P-WEST", 30000


def req(method, path, token=None, body=None):
    r = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    with urllib.request.urlopen(r) as resp:
        return resp.status, resp.read().decode()


def jreq(method, path, token=None, body=None):
    s, t = req(method, path, token, body)
    return s, json.loads(t)


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        print(f"  [{'OK' if cond else 'FAIL'}] {name} {detail}")

    # 页面静态资源可打开
    s, html = req("GET", "/")
    check("门户 index.html", s == 200 and "保证金笼" in html)
    for f in ("js/app.js", "js/views/cages_view.js", "js/views/bonds_view.js",
              "js/views/reconcile_view.js", "js/views/audit_view.js"):
        s, t = req("GET", f"/static/{f}")
        check(f"静态 {f}", s == 200 and len(t) > 100)
    s, cages_js = req("GET", "/static/js/views/cages_view.js")
    check("入笼页含冻笼互证条", "冻结已同抬" in cages_js)
    s, bonds_js = req("GET", "/static/js/views/bonds_view.js")
    check("账户页含活笼冻结列", "活笼冻结" in bonds_js)

    _, login = jreq("POST", "/api/login", body={"username": "clerk", "password": "123456"})
    tok = login["token"]

    _, bonds0 = jreq("GET", "/api/bonds", tok)
    b0 = next(b for b in bonds0["items"] if b["party_key"] == PARTY)
    print(f"战前 {PARTY}: balance={b0['balance']} frozen={b0['frozen']}")

    # —— 入笼页：表单默认笔（P-WEST / HT-2026-1201 / 30000 / 业主方）——
    print("== 入笼页 ==")
    s, cage = jreq("POST", "/api/cages/freeze", tok, {
        "party_key": PARTY, "contract_no": "HT-2026-1201", "amount": AMOUNT,
        "beneficiary": "业主方",
    })
    check("入笼提交成功", s == 200, cage.get("cage_code"))
    _, cages = jreq("GET", "/api/cages", tok)
    mine = [c for c in cages["items"] if c["cage_code"] == cage["cage_code"]]
    check("名册列表见新笼", len(mine) == 1 and mine[0]["status"] == "已冻结")
    _, proof = jreq("GET", f"/api/proof/{PARTY}", tok)
    check("入笼后互证真绿",
          proof["frozen_matches_live"] is True
          and proof["proof"]["left"] == b0["frozen"] + AMOUNT
          and proof["proof"]["left"] == proof["proof"]["right"],
          f"frozen={proof['proof']['left']} live={proof['proof']['right']}")

    # —— 账户页 ——
    print("== 账户页 ==")
    _, bonds1 = jreq("GET", "/api/bonds", tok)
    b1 = next(b for b in bonds1["items"] if b["party_key"] == PARTY)
    check("冻结同增", b1["frozen"] == b0["frozen"] + AMOUNT,
          f"{b0['frozen']} → {b1['frozen']}")
    check("余额不动", b1["balance"] == b0["balance"])
    check("列表活笼冻结列", b1["live_cage_available"] == b1["frozen"])
    check("列表互证符", b1["frozen_matches_live"] is True)

    # —— 保证金台账（账户明细 + 入金流水 + 关联笼）——
    print("== 保证金台账 ==")
    _, detail = jreq("GET", f"/api/bonds/{PARTY}", tok)
    check("台账明细互证符", detail["frozen_matches_live"] is True
          and detail["live_cage_available"] == detail["account"]["frozen"])
    check("台账关联笼含新笼", any(c["cage_code"] == cage["cage_code"] for c in detail["cages"]))
    check("台账入金流水在", len(detail["deposits"]) >= 1)

    # —— 对账页 ——
    print("== 对账页 ==")
    _, recon = jreq("GET", "/api/reconcile", tok)
    item = next(i for i in recon["items"] if i["cage_code"] == cage["cage_code"])
    check("新笼笼口径平衡", item["balanced"] is True and item["gap"] == 0)
    check("新笼账户侧互证符", item["frozen_matches_live"] is True)
    check("全账无失衡", recon["summary"]["unbalanced"] == 0,
          f"{recon['summary']}")
    bad = [i["cage_code"] for i in recon["items"] if not i["frozen_matches_live"]]
    check("全账户侧互证无违", bad == [], str(bad))

    # —— 审计页 ——
    print("== 审计页 ==")
    _, audits = jreq("GET", "/api/audit?limit=200", tok)
    hits = [a for a in audits["items"]
            if a["action"] == "cage_freeze" and a["target"] == cage["cage_code"]]
    check("入笼审计留痕", len(hits) == 1 and hits[0]["actor"] == "clerk")
    _, contention = jreq("GET", "/api/contention", tok)
    _, locks = jreq("GET", "/api/locks", tok)
    check("争用/持锁面可查", isinstance(contention["items"], list)
          and isinstance(locks["items"], list))
    check("入笼后锁已释", all(l["lock_key"] != f"bond:{PARTY}" for l in locks["items"]))

    print("RESULT:", "五页账实相符：新笼与冻结同增" if ok else "仍有账实不符")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
