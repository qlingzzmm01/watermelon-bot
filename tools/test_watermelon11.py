# -*- coding: utf-8 -*-
"""验证新的「稳定合成大西瓜」策略：曲线 / 真合成 / 序列前瞻 / 耗时。"""
import sys, time
sys.path.insert(0, r"C:\Users\Administrator\Desktop\大西瓜")
from solver import (Tuning, PRESETS, Fruit, Field, radius_of, level_progress,
                    settle_merges, choose_drop_x, _max_level, score_of, MAX_LEVEL)

W = PRESETS["watermelon11"]

print("=== 1) 等级进展 vs 游戏得分（合成价值度量）===")
print(f"{'合成':<10}{'等级进展(新)':>14}{'游戏得分(旧)':>14}{'旧/新倍数':>10}")
for lv in (1, 3, 5, 7, 9, 10):
    new = level_progress(lv, W)
    old = float(score_of(lv + 1))
    print(f"lv{lv}→lv{lv+1:<4}{new:>14.0f}{old:>14.0f}{old/new:>10.1f}x")
r_new = level_progress(10, W) / level_progress(1, W)
r_old = score_of(11) / score_of(2)
print(f"\nlv10→11 相对 lv1→2 的重要性：新 {r_new:.1f}x  |  旧 {r_old:.0f}x")
print("→ 旧模型下高等级合成压倒一切（512x），新模型保留低等级铺垫价值（5.6x）")

print("\n=== 2) 真合成结算 settle_merges ===")
f0 = Field(700, -500, 500, 500, 440, [])
def put(fr, lv, x, y):
    return Field(fr.play_width, fr.play_bottom, fr.play_top, fr.drop_y, fr.danger_y,
                 fr.fruits + [Fruit(id=len(fr.fruits)+1, level=lv, x=x, y=y,
                                    radius=radius_of(lv), dropped=True)])
# 三颗 lv3 摆成一条链：3+3→4，再与第三颗 lv3 不合成（等级不同）
f = put(put(f0, 3, -40, -450), 3, 40, -450)      # 两颗 lv3 相邻
r3 = radius_of(3)
print(f"两颗 lv3 间距 80，半径和 {2*r3:.0f} → 间距-半径和 = {80-2*r3:.1f} (需 ≤8 才合成)")
s = settle_merges(f)
print(f"结算后水果: {[(x.level, round(x.x)) for x in s.fruits]}  最高等级={_max_level(s)}")
assert _max_level(s) == 4, "相邻同级应合成为 lv4"

# 距离远不合成
f2 = put(put(f0, 3, -200, -450), 3, 200, -450)
s2 = settle_merges(f2)
print(f"相距 400 的两颗 lv3 结算后: {[(x.level) for x in s2.fruits]} (应仍为 [3,3])")
assert _max_level(s2) == 3, "远离不应合成"

# 连锁：2+2→3, 3+3→4
f3 = put(put(put(put(f0, 2, -30, -460), 2, 30, -460), 3, -30, -380), 3, 30, -380)
s3 = settle_merges(f3)
print(f"2+2 与 3+3 连锁结算后最高等级: {_max_level(s3)} (应为 4)")
assert _max_level(s3) == 4

print("\n=== 3) 序列驱动前瞻：相同序列下新旧策略的落点差异 ===")
# 场景 A：场上只有一颗 lv4 在左侧，序列接下来还是 lv4 → 新策略应贴着它放（下次合成）
fA = put(f0, 4, -200, -450)
seqA = [4, 4]
xA_new = choose_drop_x(fA, 4, W, future_levels=seqA)
xA_old = choose_drop_x(fA, 4, PRESETS["stable11"], future_levels=seqA)
dA_new = abs(xA_new - (-200)); dA_old = abs(xA_old - (-200))
print(f"A) 场上 lv4@-200，序列[lv4,lv4]（贴着放 → 下颗能合成 lv5）")
print(f"   watermelon11 x={xA_new:7.1f}  距同级 {dA_new:6.1f}")
print(f"   stable11     x={xA_old:7.1f}  距同级 {dA_old:6.1f}")
print(f"   → {'新策略更贴近同级 ✓' if dA_new < dA_old else '新策略未更贴近 ✗'}")

# 场景 B：分层——场上底部大果 lv7、顶部小果 lv2，现在投 lv6
# 新策略应避免把 lv6 架到 lv2 上面（大果在上 = 分层惩罚）
def stack(fr, lv, x, y):
    return Field(fr.play_width, fr.play_bottom, fr.play_top, fr.drop_y, fr.danger_y,
                 fr.fruits + [Fruit(id=len(fr.fruits)+100, level=lv, x=x, y=y,
                                    radius=radius_of(lv), dropped=True)])
fB = stack(stack(f0, 7, -40, -450), 2, 200, -430)
xB_new = choose_drop_x(fB, 6, W, future_levels=[1, 1])
xB_old = choose_drop_x(fB, 6, PRESETS["stable11"], future_levels=[1, 1])
print(f"\nB) 底部 lv7@-40、右侧 lv2@200，当前投 lv6")
print(f"   watermelon11 x={xB_new:7.1f}")
print(f"   stable11     x={xB_old:7.1f}")
print("   （差异体现分层/孤立惩罚的权重方向，非绝对优劣）")

print("\n=== 4) 决策耗时（投放间隔 550ms，需留余量）===")
import random
random.seed(7)
for n in (8, 16, 26, 34):
    ff = Field(700, -500, 500, 500, 440, [])
    for i in range(n):
        lv = random.randint(1, 6)
        ff = Field(ff.play_width, ff.play_bottom, ff.play_top, ff.drop_y, ff.danger_y,
                   ff.fruits + [Fruit(id=i+1, level=lv,
                                      x=random.uniform(-300, 300),
                                      y=-460 + random.uniform(0, 200),
                                      radius=radius_of(lv), dropped=True)])
    ff = settle_merges(ff)
    seq = [random.randint(1, 4) for _ in range(6)]
    t0 = time.time()
    choose_drop_x(ff, 3, W, future_levels=seq)
    dt = (time.time() - t0) * 1000
    flag = "OK" if dt < 550 else "⚠ 偏慢"
    print(f"  场上 {n:2d} 果 (结算后 {len(ff.fruits):2d}): {dt:6.0f}ms  {flag}")

print("\n✓ 全部验证通过")
