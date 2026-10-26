# -*- coding: utf-8 -*-
"""Tuning 类型回归测试：防止「float 传给 range()」崩溃复发。

背景：solver.py 使用 `from __future__ import annotations`，dataclass 字段的
`.type` 是字符串 'int' 而非类型对象。旧版 server.tuning_from_dict 用
`f.type is int` 判断，永远为 False → samples 等整型字段被转成 float →
choose_drop_x 里 `range(n_coarse)` 抛
    TypeError: 'float' object cannot be interpreted as an integer
用户在 Web 控制台拖动任一滑块后点「开始」必现。

运行：python tools/test_tuning_types.py
"""
import os
import sys
from dataclasses import fields, replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
from solver import (Tuning, choose_drop_x, parse_debug_state,  # noqa: E402
                    settle_merges)

FAILS = []


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        FAILS.append(msg)


def mkstate(n):
    fr = [{"id": i, "level": (i % 4) + 1,
           "x": -200 + (i % 7) * 60, "y": -480 + (i // 7) * 40,
           "radius": 22, "dropped": True, "vx": 0, "vy": 0} for i in range(n)]
    return {"layout": {"playWidth": 684, "playBottom": -504, "playTop": 480,
                       "dropY": 520, "dangerY": 452},
            "fruits": fr, "currentFruitId": 1,
            "isPlaying": True, "gameOver": False}


def main():
    int_fields = [f.name for f in fields(Tuning)
                  if str(f.type).strip() == "int"
                  or getattr(f.type, "__name__", "") == "int"]
    print("=" * 62)
    print(f"① 整型字段识别: {int_fields}")
    print("=" * 62)

    # 1. 前端/JSON 常见载荷：整数字段以 float 传入
    payload = {k: float(getattr(Tuning(), k)) for k in int_fields}
    payload.update({"merge_reward": 4000.0, "beam_width": 2.0})
    t = server.tuning_from_dict(payload)
    for k in int_fields:
        v = getattr(t, k)
        check(isinstance(v, int), f"tuning_from_dict 后 {k} 为 int（实际 {type(v).__name__}={v!r}）")

    print()
    print("=" * 62)
    print("② 各种输入形态都不崩")
    print("=" * 62)
    cases = [
        ("int 载荷", {"samples": 96, "sim_max_iter": 900}),
        ("float 载荷", {"samples": 96.0, "sim_max_iter": 900.0}),
        ("小数 float", {"samples": 96.7, "sim_max_iter": 900.9}),
        ("字符串数字", {"samples": "96", "sim_max_iter": "900"}),
        ("非法值", {"samples": "abc", "sim_max_iter": None}),
    ]
    for desc, d in cases:
        tt = server.tuning_from_dict(d)
        try:
            choose_drop_x(parse_debug_state(mkstate(5))[0], 1, tt)
            check(True, f"{desc}: choose_drop_x 正常")
        except Exception as e:
            check(False, f"{desc}: {type(e).__name__}: {e}")

    print()
    print("=" * 62)
    print("③ 绕过入口直接构造 float Tuning（防御层）")
    print("=" * 62)
    ft = replace(Tuning(), samples=96.0, sim_max_iter=900.0,
                 beam_width=1.0, beam_depth=2.0)
    for n in (3, 10, 18, 30):
        field, _, _ = parse_debug_state(mkstate(n))
        try:
            choose_drop_x(field, 1, ft, future_levels=[2, 3, 1, 4, 2],
                          future_depth=2)
            check(True, f"果数 {n:2d}: 不崩")
        except Exception as e:
            check(False, f"果数 {n:2d}: {type(e).__name__}: {e}")

    try:
        settle_merges(parse_debug_state(mkstate(6))[0], max_rounds=3.0)
        check(True, "settle_merges(max_rounds=3.0) 不崩")
    except Exception as e:
        check(False, f"settle_merges: {type(e).__name__}: {e}")

    print()
    print("=" * 62)
    if FAILS:
        print(f"❌ 失败 {len(FAILS)} 项")
        sys.exit(1)
    print("✅ 全部通过：整型参数不会被 float 污染，range() 不再崩溃。")


if __name__ == "__main__":
    main()
