# -*- coding: utf-8 -*-
"""真实物理落点抓包器 v2（不用 solver 模拟）。

进真实对局，故意把水果投到【已有水果堆上】制造碰撞滚动，
高频轮询 getDebugState() 追踪「刚投那颗（target_id）从挂载→物理→静止」的轨迹，
得到实体碰撞体积下的真实落点。
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as bot_mod
from solver import radius_of

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "landing_traces.json")
STATE_POLL = 0.04          # 40ms 高频轮询
SETTLE_EPS = 12.0          # 速度阈值（与 bot 一致）
LOW = -342.0               # 场地可投 x 下限（playWidth 684/2 约 342，留半径余量）
HIGH = 342.0


def main(n_drops: int = 60, max_minutes: float = 20.0):
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

    deadline = time.time() + max_minutes * 60
    traces = []
    game_no = 0
    played = False
    last_drop = 0.0
    dropped_total = 0
    rng = random.Random(42)

    while time.time() < deadline and len(traces) < n_drops:
        if b._stop.is_set():
            break
        try:
            st = frame.evaluate(bot_mod.READ_STATE_JS)
        except Exception:
            time.sleep(0.5)
            continue
        if not st:
            time.sleep(0.2)
            continue

        if st.get("isPlaying"):
            played = True
        if st.get("gameOver") and played:
            game_no += 1
            print(f"\n=== 第{game_no}局结束（已采 {len(traces)} 轨迹），刷新重开 ===")
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
            except Exception:
                pass
            time.sleep(8)
            played = False
            last_drop = 0.0
            continue

        if not st.get("isPlaying"):
            time.sleep(0.5)
            continue

        # 等场上物理静止
        fruits = st.get("fruits") or []
        moving = [f for f in fruits
                  if f.get("dropped") and not f.get("merging")
                  and abs(f.get("vx") or 0) + abs(f.get("vy") or 0) > SETTLE_EPS]
        if moving or st.get("droppingFruitId") is not None:
            time.sleep(0.1)
            continue
        now = time.time()
        if now - last_drop < 0.7:
            time.sleep(0.1)
            continue

        # 目标水果（即将投放的那颗）：
        cur_id = st.get("currentFruitId")
        cur_level = None
        for f in fruits:
            if f["id"] == cur_id:
                cur_level = f["level"]
                break
        lv = cur_level or 1
        r = radius_of(lv)

        # 投放点：挑【堆顶较高】的水果，投它上方偏 0.4r 处 → 必然砸在斜坡上滚动
        settled = [f for f in fruits if f.get("dropped") and not f.get("merging")]
        if settled:
            # 堆顶高度 = y + radius（y 越大地势越高）
            tops = sorted(settled, key=lambda f: (f["y"] or 0) + radius_of(f["level"]), reverse=True)[:5]
            tgt = rng.choice(tops)
            rr = radius_of(tgt["level"]) + r
            dx = tgt["x"] + rng.choice([-1.0, 1.0]) * rng.uniform(rr * 0.3, rr * 0.75)
        else:
            dx = rng.uniform(-200.0, 200.0)
        dx = max(LOW + r, min(HIGH - r, dx))

        # 投放前现场快照（供 solver 回放校准）：所有已落静止水果
        snapshot = [{"level": f.get("level"), "x": f.get("x"), "y": f.get("y"),
                     "radius": f.get("radius") or radius_of(f.get("level", 1))}
                    for f in settled]

        # 记录投放前状态：当前挂载水果 id + 场上已落 id
        target_id = cur_id
        dropped_ids = {f["id"] for f in fruits if f.get("dropped")}
        # 若 target_id 恰好已 dropped（旧局面），取未落挂载的那颗
        if target_id in dropped_ids or target_id is None:
            hang = [f for f in fruits if not f.get("dropped")]
            target_id = hang[0]["id"] if hang else None
        if target_id is None:
            time.sleep(0.2)
            continue

        try:
            frame.evaluate(bot_mod.DROP_JS, float(dx))
        except Exception as e:
            print(f"[drop err] {e}")
            continue
        last_drop = now
        dropped_total += 1

        # 高频追踪 target_id：挂载(dropped=false) → 物理(dropped=true) → 真静止
        # ⚠ 游戏物理时间缩放很慢，水果落地需数秒：静止须「速度≈0 且连续 5 帧位置不变」
        trace = {"game": game_no + 1, "n": dropped_total, "drop_x": round(dx, 1),
                 "level": lv, "target_id": target_id, "snapshot": snapshot, "pts": []}
        t_start = time.time()
        stable_frames = 0
        last_x = last_y = None
        while time.time() - t_start < 20.0:
            time.sleep(STATE_POLL)
            try:
                st2 = frame.evaluate(bot_mod.READ_STATE_JS)
            except Exception:
                continue
            if not st2:
                continue
            if st2.get("gameOver") or not st2.get("isPlaying"):
                trace["note"] = "round-ended"
                break
            f = next((g for g in (st2.get("fruits") or []) if g["id"] == target_id), None)
            if f is None:
                # target 消失 → 参与合成
                trace["note"] = "merged-away"
                break
            fx_, fy_ = f.get("x"), f.get("y")
            if last_x is not None:
                moved = abs(fx_ - last_x) + abs(fy_ - last_y)
                speed = abs(f.get("vx") or 0) + abs(f.get("vy") or 0)
                if f.get("dropped") and not f.get("merging") and moved < 0.6 and speed < 1.2:
                    stable_frames += 1
                else:
                    stable_frames = 0
                if stable_frames >= 5:
                    # 真静止
                    trace["pts"].append({
                        "t": round(time.time() - t_start, 2),
                        "x": round(fx_, 1), "y": round(fy_, 1),
                        "dropped": True, "vx": f.get("vx"), "vy": f.get("vy"),
                        "merging": f.get("merging"), "settled": True,
                    })
                    trace["note"] = "settled"
                    break
            last_x, last_y = fx_, fy_
            # 抽稀记录（记录关键帧：进入物理 + 每 0.5s）
            if not trace["pts"] or time.time() - t_start - trace["pts"][-1]["t"] > 0.5 \
                    or (not f.get("dropped") and len(trace["pts"]) < 12):
                trace["pts"].append({
                    "t": round(time.time() - t_start, 2),
                    "x": round(fx_, 1), "y": round(fy_, 1),
                    "dropped": f.get("dropped"), "vx": f.get("vx"), "vy": f.get("vy"),
                    "merging": f.get("merging"),
                })

        if trace["pts"]:
            settled_pt = next((p for p in reversed(trace["pts"]) if p.get("settled")),
                              trace["pts"][-1])
            trace["land_x"] = settled_pt["x"]
            trace["land_y"] = settled_pt["y"]
            first_phys = next((p for p in trace["pts"] if p.get("dropped")), None)
            trace["first_phys_x"] = first_phys["x"] if first_phys else None
            trace["npts"] = len(trace["pts"])
            traces.append(trace)
            if trace.get("land_x") is not None:
                print(f"[#{len(traces):3d}] lv{lv} 投x={dx:6.0f} → "
                      f"静止({trace['land_x']:6.1f},{trace['land_y']:6.1f}) "
                      f"Δx={trace['land_x']-dx:+7.1f}  {trace.get('note','')}")
            else:
                print(f"[#{len(traces):3d}] lv{lv} 投x={dx:6.0f} → {trace.get('note','?')}")
        else:
            print(f"[miss] lv{lv} 投x={dx:.0f} 12s 无轨迹")

    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(traces, fp, ensure_ascii=False, indent=1)
    print(f"\n已保存 {len(traces)} 条轨迹 → {OUT}")
    if traces:
        settled_t = [t for t in traces if t.get("land_x") is not None]
        if settled_t:
            dxs = [t["land_x"] - t["drop_x"] for t in settled_t]
            print(f"落到静止 {len(settled_t)} 颗: 平均Δx={sum(dxs)/len(dxs):+.1f}px  "
                  f"最大|Δx|={max(abs(d) for d in dxs):.0f}px")
        merged = [t for t in traces if t.get("note") == "merged-away"]
        if merged:
            print(f"投后即合成(追丢) {len(merged)} 颗")
    b.close()


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    main(n_drops=n)
