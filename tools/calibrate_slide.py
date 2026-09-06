# -*- coding: utf-8 -*-
"""用真实抓包样本校准 simulate_drop 的滚落系数 roll_factor。

对每条真实样本：在「投放前现场快照」上跑 simulate_drop(投放x)，
比较预测静止位与真实静止位，扫描 roll_factor 找最小平均偏差。
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import solver
from solver import PRESETS, Field, Fruit, radius_of, simulate_drop
from dataclasses import replace

TRACES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "landing_traces.json")
W = PRESETS["watermelon11"]

# 真实场地布局（grab 数据里静止 y ∈ [-460, -49]，playBottom=-504，地面≈-486）
PW, PB, PT, DY, DGY = 684.0, -504.0, 480.0, 506.0, 452.0


def rebuild(snap):
    """按快照重建 Field（坐标直接用真实 debug 值）。"""
    fruits = [Fruit(id=-(i + 1), level=s["level"], x=s["x"], y=s["y"],
                    radius=s.get("radius") or radius_of(s["level"]),
                    dropped=True)
              for i, s in enumerate(snap)]
    return Field(PW, PB, PT, DY, DGY, fruits)


def main():
    data = json.load(open(TRACES, encoding="utf-8"))
    samples = [t for t in data if t.get("note") == "settled"
               and t.get("land_x") is not None and t.get("snapshot")]
    print(f"可校准真静止样本: {len(samples)} / {len(data)}\n")

    # 扫描 roll_factor
    print(f"{'roll':>6} {'平均欧氏偏差':>10} {'平均|Δx|':>9} {'Δx>100占比':>10} {'Δy均值':>8}")
    results = []
    for rf in [x / 20 for x in range(0, 31)]:          # 0.00 ~ 1.50
        tun = replace(W, roll_factor=rf)
        devs, dxs, dys = [], [], []
        for s in samples:
            f = rebuild(s["snapshot"])
            r = radius_of(s["level"])
            px, py = simulate_drop(f, s["drop_x"], r, tun)
            devs.append(math.hypot(px - s["land_x"], py - s["land_y"]))
            dxs.append(abs(px - s["land_x"]))
            dys.append(abs(py - s["land_y"]))
        avg_dev = sum(devs) / len(devs)
        avg_dx = sum(dxs) / len(dxs)
        over100 = sum(1 for d in dxs if d > 100) / len(dxs) * 100
        avg_dy = sum(dys) / len(dys)
        results.append((rf, avg_dev, avg_dx, over100, avg_dy))
        print(f"{rf:6.2f} {avg_dev:10.1f} {avg_dx:9.1f} {over100:10.0f}% {avg_dy:8.1f}")

    best = min(results, key=lambda t: t[1])
    print(f"\n最优 roll_factor = {best[0]:.2f}  "
          f"平均欧氏偏差 {best[1]:.1f}px（基线 0.85 → {results[int(0.85*20)][1]:.1f}px）")

    # 基线对比细节
    rf0 = 0.85
    print(f"\n--- roll_factor={rf0:.2f}（当前默认）在真实样本上的逐条对比 ---")
    tun = replace(W, roll_factor=rf0)
    print(f"{'lv':>3}{'投x':>8}{'预测x':>9}{'真实x':>9}{'Δx':>8}{'预测y':>9}{'真实y':>9}{'Δy':>7}")
    for s in samples[:15]:
        f = rebuild(s["snapshot"])
        px, py = simulate_drop(f, s["drop_x"], radius_of(s["level"]), tun)
        print(f"{s['level']:>3}{s['drop_x']:>8.0f}{px:>9.1f}{s['land_x']:>9.1f}"
              f"{px-s['land_x']:>8.1f}{py:>9.1f}{s['land_y']:>9.1f}{py-s['land_y']:>7.1f}")


if __name__ == "__main__":
    main()
