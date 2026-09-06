# -*- coding: utf-8 -*-
"""实际落点校准采集器。

目标：采集真实对局里每次投放的「预测静止位 vs 实际静止位」，
统计 simulate_drop 模拟与真实物理（WASM Box2D + 实体碰撞体积）的系统偏差，
用于校准滑移系数等模拟参数。

样本：(投放x, 预测静止x/y, 实际静止x/y, level)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as bot_mod
import solver
from solver import PRESETS, parse_debug_state, choose_drop_x, simulate_drop, radius_of

W = PRESETS["watermelon11"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "landing_samples.json")


def main(target_samples: int = 200, max_minutes: float = 15.0):
    b = bot_mod.WatermelonBot(log=lambda m: print(f"[bot] {m}"))
    b._ensure_browser(False)
    page = b._page
    if "douyu.com" not in (page.url or ""):
        page.goto(bot_mod.ROOM_URL, wait_until="domcontentloaded", timeout=60000)

    frame = b._find_game_frame(page, timeout=600)
    if frame is None:
        print("未找到游戏 frame"); b.close(); return
    print(f"游戏 frame: {frame.url[:100]}")

    bound = False
    for _ in range(60):
        r = frame.evaluate(bot_mod.FIND_GAME_JS)
        if r and r.get("ok"):
            bound = True
            break
        time.sleep(1)
    if not bound:
        print("无法绑定游戏组件"); b.close(); return
    print("已绑定游戏组件，等待对局自动开始…")

    samples = []
    deadline = time.time() + max_minutes * 60
    game_no = 0
    last_drop = 0.0
    played = False

    while time.time() < deadline and len(samples) < target_samples:
        if b._stop.is_set():
            break
        try:
            st = frame.evaluate(bot_mod.READ_STATE_JS)
        except Exception:
            time.sleep(1)
            continue
        if not st:
            time.sleep(0.3)
            continue

        if st.get("isPlaying"):
            played = True
        if st.get("gameOver") and played:
            game_no += 1
            print(f"\n=== 第 {game_no} 局结束，已采集 {len(samples)} 样本 ===")
            # 刷新重开
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
                print("已刷新，等下一局…")
            except Exception:
                pass
            time.sleep(8)
            played = False
            continue

        if not st.get("isPlaying"):
            time.sleep(0.6)
            continue

        field, level, settled = parse_debug_state(st)
        if level is None or not settled:
            time.sleep(0.15)
            continue
        now = time.time()
        if now - last_drop < 0.55:
            time.sleep(0.08)
            continue

        # 读序列（有就用，没有随机等级兜底——反正只测落点模拟）
        q = None
        try:
            q = frame.evaluate(bot_mod.READ_FRUIT_QUEUE_JS)
        except Exception:
            pass
        future = []
        if q and q.get("next"):
            future = [q["next"].get("level")] + [r.get("level") for r in (q.get("rest") or [])][:5]
        else:
            future = [level] * 3

        r = radius_of(level)
        x = choose_drop_x(field, level, W, future_levels=future)
        px, py = simulate_drop(field, x, r, W)          # 预测静止位
        ids_before = {f["id"] for f in st.get("fruits") or [] if f.get("dropped")}

        try:
            frame.evaluate(bot_mod.DROP_JS, float(x))
        except Exception as e:
            print(f"[drop err] {e}")
            continue
        last_drop = now

        # 等新水果静止，抓真实落点
        real = None
        for _ in range(90):            # 最长 ~9s
            time.sleep(0.1)
            try:
                st2 = frame.evaluate(bot_mod.READ_STATE_JS)
            except Exception:
                continue
            if not st2:
                continue
            cands = [f for f in (st2.get("fruits") or [])
                     if f.get("dropped") and f["id"] not in ids_before
                     and not f.get("merging")
                     and abs(f.get("vx") or 0) + abs(f.get("vy") or 0) < 12.0]
            if cands:
                f = cands[0]
                real = (f["x"], f["y"])
                break
            # 若对局已结束/刷新，放弃这颗
            if st2.get("gameOver") or not st2.get("isPlaying"):
                break

        if real:
            samples.append({
                "game": game_no + 1,
                "level": level,
                "drop_x": round(x, 1),
                "pred_x": round(px, 1),
                "pred_y": round(py, 1),
                "real_x": round(real[0], 1),
                "real_y": round(real[1], 1),
            })
            dev = ((px - real[0]) ** 2 + (py - real[1]) ** 2) ** 0.5
            print(f"[#{len(samples):3d}] lv{level} drop@x={x:7.1f} | "
                  f"预测({px:6.1f},{py:6.1f}) 实际({real[0]:6.1f},{real[1]:6.1f}) "
                  f"偏差{dev:5.1f}px")
        else:
            print(f"[miss] lv{level} 投放后未捕捉到静止水果（可能合成或被顶开）")

    # 汇总
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(samples, fp, ensure_ascii=False, indent=1)
    print(f"\n已保存 {len(samples)} 样本 → {OUT}")
    if samples:
        dx = [abs(s["pred_x"] - s["real_x"]) for s in samples]
        dy = [abs(s["pred_y"] - s["real_y"]) for s in samples]
        dev = [( (s["pred_x"]-s["real_x"])**2 + (s["pred_y"]-s["real_y"])**2 )**0.5 for s in samples]
        bx = [s["pred_x"] - s["real_x"] for s in samples]   # 有符号偏差：正=预测偏右
        print(f"样本数: {len(samples)}")
        print(f"平均|Δx|={sum(dx)/len(dx):.1f}px  平均|Δy|={sum(dy)/len(dy):.1f}px  "
              f"平均欧氏偏差={sum(dev)/len(dev):.1f}px")
        print(f"x有符号偏差: 均值={sum(bx)/len(bx):+.1f}px（正=预测偏右）")
        # 按等级分组
        for lv in sorted({s["level"] for s in samples}):
            sub = [s for s in samples if s["level"] == lv]
            bx2 = [s["pred_x"] - s["real_x"] for s in sub]
            print(f"  lv{lv}: n={len(sub)}  平均Δx={sum(bx2)/len(bx2):+.1f}px")
    b.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    main(target_samples=n)
