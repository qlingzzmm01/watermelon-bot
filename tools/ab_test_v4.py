# -*- coding: utf-8 -*-
"""离线 A/B：watermelon11 v4（配对感知+深前瞻）vs 关闭配对感知(v1 行为)。
各 8 局、相同序列种子，比较最高等级与大西瓜数（离线物理偏保守，看相对差异）。
"""
import sys, time, random
from dataclasses import replace
sys.path.insert(0, r"C:\Users\Administrator\Desktop\大西瓜")
from solver import (PRESETS, Fruit, Field, radius_of, settle_merges,
                    choose_drop_x, simulate_drop, _place_fruit, _max_level)

PW, PB, PT, DY, DGY = 684.0, -504.0, 480.0, 506.0, 452.0
W = PRESETS["watermelon11"]
V1 = replace(W, pair_boost=1.0, pair_decay=1.0)   # 关闭配对感知 = v1 行为


def make_seq(rng, n=420):
    pool = (1, 1, 1, 1, 1, 2, 2, 2, 3, 3, 4, 5)
    return [rng.choice(pool) for _ in range(n)]


def run_battle(tuning, seq, max_drops=280, lookahead=9):
    f = Field(PW, PB, PT, DY, DGY, [])
    for i, lv in enumerate(seq[:max_drops]):
        future = seq[i + 1: i + 1 + lookahead]
        x = choose_drop_x(f, lv, tuning, future_levels=future)
        r = radius_of(lv)
        fx, fy = simulate_drop(f, x, r, tuning)
        if fy + r > DGY:
            return _max_level(f), i, True
        f = _place_fruit(f, lv, fx, fy, r)
        f = settle_merges(f)
    return _max_level(f), min(len(seq), max_drops), False


if __name__ == "__main__":
    n_games = 8
    res = {"v4": [], "v1": []}
    for g in range(n_games):
        rng = random.Random(500 + g)
        seq = make_seq(rng)
        row = []
        for name, t in (("v4", W), ("v1", V1)):
            t0 = time.perf_counter()
            lv, dp, died = run_battle(t, seq)
            res[name].append(lv)
            row.append(f"{name}: lv{lv}({dp}投) {time.perf_counter()-t0:.0f}s")
        print(f"第{g+1}局  " + "  ".join(row))
    for name in ("v4", "v1"):
        rs = res[name]
        wm = sum(1 for x in rs if x >= 11)
        print(f"\n{name}: 平均 {sum(rs)/len(rs):.1f}  最高 {max(rs)}  西瓜局 {wm}/{n_games}  "
              f"逐局 [{','.join('lv'+str(x) for x in rs)}]")
