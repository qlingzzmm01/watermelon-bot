# -*- coding: utf-8 -*-
"""v7 三架构方案各 5 局：wm-R(鲁棒) → wm-H(Beam) → wm-D(数据节奏) → 汇总。"""
import json, time, urllib.request, sys, os

URL = "http://127.0.0.1:8733"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rounds_result.txt")


def api(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else b"{}"
    req = urllib.request.Request(URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(opener.open(req, timeout=10).read().decode())


def wait_games(n, tag, results, base=0, deadline_s=3000):
    seen = base
    t0 = time.time()
    while len(results) < base + n and time.time() - t0 < deadline_s:
        try:
            d = api("/api/status")
        except Exception:
            time.sleep(10); continue
        hist = d.get("gameHistory") or []
        for g in hist[seen:]:
            print(f"[{tag}] 局{g['game']}: lv{g['bestLevel']} 西瓜x{g['watermelons']} "
                  f"分{g['score']} 投{g['drops']}", flush=True)
            results.append(g)
        seen = len(hist)
        if len(results) >= base + n:
            break
        time.sleep(20)
    return len(results)


def main():
    plan = [("wm-R", 5, "R"), ("wm-H", 5, "H"), ("wm-D", 5, "D")]
    results = []
    for preset, n, tag in plan:
        print(f"=== 切 {preset}，等 {n} 局 ===", flush=True)
        try:
            r = api("/api/preset", {"name": preset})
            print("切预设:", r.get("ok"), r.get("name"), flush=True)
        except Exception as e:
            print("切预设失败:", e, flush=True)
            return
        wait_games(n, tag, results, len(results))

    # 汇总
    lines = []
    idx = 0
    summary = []
    for preset, n, tag in plan:
        seg = results[idx:idx + n]
        idx += n
        lines.append(f"=== {preset} ({tag}) {n}局 ===")
        for g in seg:
            lines.append(f"  局{g['game']}: lv{g['bestLevel']} 西瓜x{g['watermelons']} "
                         f"分{g['score']} 投{g['drops']}")
        lvs = [g["bestLevel"] for g in seg]
        wm = sum(g["watermelons"] for g in seg)
        avg = sum(lvs) / len(lvs) if lvs else 0
        lines.append(f"  平均 lv{avg:.1f}  西瓜 {wm}")
        summary.append((preset, avg, wm))
        lines.append("")
    lines.append("=== 总对比 ===")
    for p, avg, wm in summary:
        lines.append(f"  {p}: 平均 lv{avg:.1f} / 西瓜 {wm}")
    txt = "\n".join(lines)
    print("\n" + txt, flush=True)
    open(OUT, "w", encoding="utf-8").write(txt)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
