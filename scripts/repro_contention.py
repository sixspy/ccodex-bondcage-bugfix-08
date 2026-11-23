"""争用复现：双会话抢锁 + 并发双请求打同一当事人账户入笼。

口径（审锁纪要 2026-11-23 封存）：
- 双会话：先到持锁未释，后到入笼须 409，只记争用不死磕成功态，业务零写。
- 并发突发：响应只允许 200/409；每个 409 须有争用痕；
  冻结抬升总额必须恰等于 成功笔数 × 单笔金额（冻笼同增，不多不少）。
仅打本机文件库上的服务。用法：python scripts/repro_contention.py [base_url]
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11333"
PARTY = "P-NORTH"  # 种子户：余额 280000 / 冻结 80000
AMOUNT = 50000


def req(method, path, token, body=None):
    r = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def login(u):
    _, j = req("POST", "/api/login", None, {"username": u, "password": "123456"})
    return j["token"]


def state(tok):
    _, acct = req("GET", f"/api/bonds/{PARTY}", tok)
    _, cages = req("GET", "/api/cages", tok)
    _, cont = req("GET", "/api/contention", tok)
    n = len([c for c in cages["items"] if c["party_key"] == PARTY])
    hits = [c for c in cont["items"] if c["lock_key"] == f"bond:{PARTY}"]
    return acct["account"]["frozen"], n, len(hits)


def main() -> int:
    tok_a, tok_b = login("admin"), login("clerk")
    ok = True

    # —— 段一：双会话抢锁（ deterministic ）——
    f0, n0, c0 = state(tok_a)
    s, _ = req("POST", "/api/locks/acquire", tok_a, {"lock_key": f"bond:{PARTY}"})
    assert s == 200, f"admin 抢锁失败 {s}"
    s_late, j_late = req("POST", "/api/cages/freeze", tok_b, {
        "party_key": PARTY, "contract_no": "HT-RACE-LATE", "amount": AMOUNT,
        "beneficiary": "争用复现", "cage_code": "CG-RACE-LATE",
    })
    f1, n1, c1 = state(tok_a)
    req("POST", "/api/locks/release", tok_a, {"lock_key": f"bond:{PARTY}"})
    seg1 = (s_late == 409 and c1 == c0 + 1 and f1 == f0 and n1 == n0)
    ok &= seg1
    print(f"[段一·双会话] 后到入笼 HTTP {s_late}（{j_late.get('detail')}） "
          f"争用痕 +{c1 - c0} 冻结零改={f1 == f0} 笼零增={n1 == n0} "
          f"{'OK' if seg1 else '<<< 事故'}")

    # —— 段二：并发双请求突发 ——
    f0, n0, c0 = state(tok_a)
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def strike(i):
        tok = tok_a if i % 2 == 0 else tok_b
        barrier.wait()
        s, j = req("POST", "/api/cages/freeze", tok, {
            "party_key": PARTY, "contract_no": f"HT-RACE-{i}", "amount": AMOUNT,
            "beneficiary": "争用复现", "cage_code": f"CG-RACE-{i:02d}",
        })
        with lock:
            results.append((s, j.get("detail") or ""))

    ts = [threading.Thread(target=strike, args=(i,)) for i in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()
    f1, n1, c1 = state(tok_a)

    n200 = sum(1 for s, _ in results if s == 200)
    contention_409 = sum(1 for s, d in results if s == 409 and "争用" in d)
    business_409 = sum(1 for s, d in results if s == 409 and "争用" not in d)
    only_expected = all(s in (200, 409) for s, _ in results)
    sync = (f1 - f0) == AMOUNT * n200 and (n1 - n0) == n200
    # 争用 409 必须笔笔有痕；业务 409（可用不足）走拒写审计，不占争用痕
    cont_ok = (c1 - c0) >= contention_409
    seg2 = only_expected and sync and cont_ok and n200 >= 1
    ok &= seg2
    print(f"[段二·并发突发] 200×{n200} 争用409×{contention_409} 业务409×{business_409} "
          f"冻结抬 {f1 - f0}（= {AMOUNT}×{n200}）新笼 {n1 - n0} 争用痕 +{c1 - c0} "
          f"{'OK' if seg2 else '<<< 事故'}")

    print("RESULT:", "争用处理正确：后到只记争用，冻笼同增" if ok else "事故仍在")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
