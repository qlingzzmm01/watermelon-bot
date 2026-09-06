# -*- coding: utf-8 -*-
"""wm-A vs wm-B 各 3 局自动测试：切换预设→等3局→切换→等3局→汇总写文件。"""
import json, time, urllib.request, sys, os

URL = "http://127.0.0.1:8733"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rounds_result.txt")


def api(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else b"{}"
    req = urllib.request.Request(URL + path, data=data,
                                 headers={"Content-Type": "application/json"})
    return json.loads(opener.open(req, timeout=10).read().decode())


def wait_games(n, tag, results, base=0):
    seen = base
    while len(results) < base + n:
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
    results = []
    # 方案 A
    print("=== 切 wm-A，等 3 局 ===", flush=True)
    r = api("/api/preset", {"name": "wm-A"})
    print("切预设:", r.get("ok"), r.get("name"), flush=True)
    wait_games(3, "A", results, 0)
    # 方案 B
    print("=== 切 wm-B，等 3 局 ===", flush=True)
    r = api("/api/preset", {"name": "wm-B"})
    print("切预设:", r.get("ok"), r.get("name"), flush=True)
    wait_games(3, "B", results, len(results))

    # 汇总：前 3 = A，后 3 = B
    a = results[:3]; b = results[3:6]
    lines = ["=== 方案A (全局空间管理) 3局 ==="]
    for g in a:
        lines.append(f"  局{g['game']}: lv{g['bestLevel']} 西瓜x{g['watermelons']} "
                     f"分{g['score']} 投{g['drops']}")
    la = [g["bestLevel"] for g in a]; wa = sum(g["watermelons"] for g in a)
    lines.append(f"  A 平均 lv{sum(la)/len(la):.1f}  西瓜 {wa}")
    lines.append("")
    lines.append("=== 方案B (保命优先) 3局 ===")
    for g in b:
        lines.append(f"  局{g['game']}: lv{g['bestLevel']} 西瓜x{g['watermelons']} "
                     f"分{g['score']} 投{g['drops']}")
    lb = [g["bestLevel"] for g in b]; wb = sum(g["watermelons"] for g in b)
    lines.append(f"  B 平均 lv{sum(lb)/len(lb):.1f}  西瓜 {wb}")
    txt = "\n".join(lines)
    print("\n" + txt, flush=True)
    open(OUT, "w", encoding="utf-8").write(txt)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
