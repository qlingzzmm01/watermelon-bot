# -*- coding: utf-8 -*-
"""离线对局模拟：在相同序列下对比各预设能堆到几级（验证「冲大西瓜」策略是否真的更优）。

场地参数用真实抓包值：playWidth=684, playBottom=-504, playTop=480, dropY=506, dangerY=452
"""
import sys, time, random
sys.path.insert(0, r"C:\Users\Administrator\Desktop\大西瓜")
from solver import (PRESETS, Fruit, Field, radius_of, settle_merges,
                    choose_drop_x, simulate_drop, _place_fruit, _max_level, MAX_LEVEL)

# 真实场地（2026-09-01 抓包 layout）
PW, PB, PT, DY, DGY = 684.0, -504.0, 480.0, 506.0, 452.0


def empty_field():
    return Field(PW, PB, PT, DY, DGY, [])


def make_seq(rng, n=400):
    """按真实观察的等级分布生成序列（小果为主）。"""
    pool = (1, 1, 1, 1, 1, 2, 2, 2, 3, 3, 4, 5)
    return [rng.choice(pool) for _ in range(n)]


def run_battle(tuning, seq, max_drops=260, lookahead=6):
    """模拟一整局。返回 (最高等级, 投放数, 是否死亡)。"""
    field = empty_field()
    for i, lv in enumerate(seq[:max_drops]):
        future = seq[i + 1: i + 1 + lookahead]
        x = choose_drop_x(field, lv, tuning, future_levels=future)
        r = radius_of(lv)
        fx, fy = simulate_drop(field, x, r, tuning)
        if fy + r > DGY:                      # 越过死亡线
            return _max_level(field), i, True
        field = _place_fruit(field, lv, fx, fy, r)
        field = settle_merges(field)
    return _max_level(field), min(len(seq), max_drops), False


if __name__ == "__main__":
    n_games = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    names = ["watermelon11", "stable11", "balanced", "score"]
    print(f"离线对局：每个策略 {n_games} 局，相同序列种子，比较最高等级\n")
    results = {n: [] for n in names}
    deaths = {n: 0 for n in names}
    drops = {n: [] for n in names}

    for g in range(n_games):
        rng = random.Random(1000 + g)          # 所有策略共用同一序列
        seq = make_seq(rng)
        row = []
        for name in names:
            t0 = time.perf_counter()
            lv, dp, died = run_battle(PRESETS[name], seq)
            dt = time.perf_counter() - t0
            results[name].append(lv)
            drops[name].append(dp)
            deaths[name] += 1 if died else 0
            row.append(f"{name}: lv{lv:<3}({dp:3d}投{'死' if died else '满'}) {dt:5.1f}s")
        print(f"第{g+1}局  " + "  ".join(row))

    print(f"\n{'策略':<15}{'平均最高等级':>12}{'最好':>7}{'最差':>7}{'平均投放':>10}{'死亡局数':>10}")
    for name in names:
        rs = results[name]
        avg = sum(rs) / len(rs)
        print(f"{name:<15}{avg:>12.1f}{max(rs):>7}{min(rs):>7}"
              f"{sum(drops[name])/len(drops[name]):>10.0f}{deaths[name]:>10}/{n_games}")
    best = max(names, key=lambda n: sum(results[n]) / len(results[n]))
    print(f"\n最优策略: {best}")
    wm = results["watermelon11"]; st = results["stable11"]
    print(f"watermelon11 平均 lv{sum(wm)/len(wm):.1f}  vs  stable11 平均 lv{sum(st)/len(st):.1f}")
