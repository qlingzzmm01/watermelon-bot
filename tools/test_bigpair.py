# -*- coding: utf-8 -*-
"""本地离线 A/B：default vs 大果配对改进，各 5 局，相同含大果序列。

序列形态贴近服务端窗口（小果为主 + 周期性补给大果段），用于复现
真实对局「37% 到 lv10 / 0% 到 lv11」的卡点并验证修复。
用法: python tools/test_bigpair.py [n_games=5]
"""
import random
import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\大西瓜")
from solver import (PRESETS, Fruit, Field, radius_of, settle_merges,
                    choose_drop_x, simulate_drop, _place_fruit, _max_level)

PW, PB, PT, DY, DGY = 684.0, -504.0, 480.0, 506.0, 452.0


def empty_field():
    return Field(PW, PB, PT, DY, DGY, [])


def make_seq(rng, n=400):
    """真实形态序列：小果为主(1-4)，随进度逐渐解锁 5-7，末端给 8-10 大果段。

    服务端实测形态：开局全小果；中盘出现 5-7；后期窗口偶发 8-10。
    """
    seq = []
    for i in range(n):
        if i < 60:
            pool = (1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 4, 4)
        elif i < 130:
            pool = (1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 5, 5, 6)
        elif i < 220:
            pool = (1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8)
        else:
            pool = (1, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10)
        seq.append(rng.choice(pool))
    return seq


def run_battle(tuning, seq, max_drops=300):
    field = empty_field()
    peak = 0
    for i, lv in enumerate(seq[:max_drops]):
        future = seq[i + 1: i + 1 + 6]
        x = choose_drop_x(field, lv, tuning, future_levels=future)
        r = radius_of(lv)
        fx, fy = simulate_drop(field, x, r, tuning)
        if fy + r > DGY:
            return _max_level(field), i, True
        field = _place_fruit(field, lv, fx, fy, r)
        field = settle_merges(field)
        peak = max(peak, _max_level(field))
    return _max_level(field), min(len(seq), max_drops), False


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    cfg = {"baseline": PRESETS["default"]}
    if len(sys.argv) > 2:
        # 变体：把传入名当 key，在 default 上叠改
        name = sys.argv[2]
        import copy
        t = copy.deepcopy(PRESETS["default"])
        cfg[name] = t

    for tag, tun in cfg.items():
        rs = []
        t0 = time.perf_counter()
        for g in range(n):
            rng = random.Random(1000 + g)
            seq = make_seq(rng)
            ml, dp, died = run_battle(tun, seq)
            rs.append((ml, dp, died))
            print(f"[{tag}] 局{g+1}: max_lv={ml} 投={dp} {'死' if died else '满'}", flush=True)
        lvs = [r[0] for r in rs]
        wm = sum(1 for r in rs if r[0] >= 11)
        dt = time.perf_counter() - t0
        print(f"[{tag}] 平均lv={sum(lvs)/len(lvs):.1f} 最高={max(lvs)} 西瓜局={wm}/{n}  {dt:.0f}s\n")


if __name__ == "__main__":
    main()
