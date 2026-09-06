# -*- coding: utf-8 -*-
"""对比新旧 simulate_drop：精度（落点差异）与速度。"""
import sys, time, random, math
sys.path.insert(0, r"C:\Users\Administrator\Desktop\大西瓜")
from solver import (Tuning, PRESETS, Fruit, Field, radius_of, settle_merges,
                    choose_drop_x, _max_level, MAX_LEVEL)

W = PRESETS["watermelon11"]


def sim_old(field, start_x, radius, tuning):
    """旧实现（保留作精度基准）。"""
    x, y = start_x, field.drop_y
    half = field.play_width / 2
    lo, hi = -half + radius, half - radius
    solids = [(f.x, f.y, f.radius) for f in field.fruits if f.dropped and not f.merging]
    it = 0
    while it < tuning.sim_max_iter:
        it += 1
        y -= tuning.sim_step
        for _ in range(4):
            overlapped = False
            for fx, fy, fr in solids:
                dx, dy = x - fx, y - fy
                d = math.hypot(dx, dy)
                need = radius + fr
                if d < need:
                    overlapped = True
                    if d < 1e-6:
                        dx, dy, d = 0.0, 1.0, 1e-6
                    nx, ny = dx / d, dy / d
                    push = need - d
                    x += nx * push
                    y += ny * push
                    slide = abs(nx) * push * 0.85
                    x += math.copysign(slide, nx) if nx != 0 else slide
            x = max(lo, min(hi, x))
            if not overlapped:
                break
        if y - radius <= field.play_bottom:
            y = field.play_bottom + radius
            break
    return x, max(y, field.play_bottom + radius)


from solver import simulate_drop as sim_new

def mkfield(n, seed):
    random.seed(seed)
    fr = []
    for i in range(n):
        lv = random.randint(1, 6)
        fr.append(Fruit(id=i + 1, level=lv, x=random.uniform(-300, 300),
                        y=-460 + random.uniform(0, 180),
                        radius=radius_of(lv), dropped=True))
    return Field(700, -500, 500, 500, 440, fr)

print("=== 精度：新旧 simulate_drop 落点差异 ===")
print(f"{'场景':<12}{'旧(x,y)':>22}{'新(x,y)':>22}{'偏差px':>9}")
worst = 0.0
for seed, n in ((1, 6), (2, 14), (3, 22), (4, 30), (5, 40)):
    f = settle_merges(mkfield(n, seed))
    for cx in (-250, -80, 60, 220):
        r = radius_of(3)
        ox, oy = sim_old(f, cx, r, W)
        nx_, ny_ = sim_new(f, cx, r, W)
        dev = math.hypot(ox - nx_, oy - ny_)
        worst = max(worst, dev)
    print(f"{n:2d}果 seed{seed}  偏差最大 {worst:6.2f}px")
print(f"\n最大落点偏差: {worst:.2f}px  ({'可接受 <8px' if worst < 8 else '⚠ 偏差过大'})")

print("\n=== 速度：单次决策耗时（投放间隔 550ms）===")
for n in (8, 16, 26, 34, 42):
    f = settle_merges(mkfield(n, 100 + n))
    seq = [random.randint(1, 4) for _ in range(6)]
    # 旧模拟
    import solver
    orig = solver.simulate_drop
    solver.simulate_drop = sim_old
    t0 = time.perf_counter(); choose_drop_x(f, 3, W, future_levels=seq); t_old = (time.perf_counter()-t0)*1000
    solver.simulate_drop = orig
    t0 = time.perf_counter(); choose_drop_x(f, 3, W, future_levels=seq); t_new = (time.perf_counter()-t0)*1000
    flag = "OK" if t_new < 400 else ("尚可" if t_new < 550 else "⚠ 偏慢")
    print(f"  场上 {n:2d} 果: 旧 {t_old:6.0f}ms → 新 {t_new:6.0f}ms  提速 {t_old/max(t_new,1):4.1f}x  {flag}")

print("\n=== 全量回归：真合成结算 + 预设可用 ===")
f0 = Field(700, -500, 500, 500, 440, [])
def put(fr, lv, x, y):
    return Field(700,-500,500,500,440, fr.fruits+[Fruit(id=len(fr.fruits)+1,level=lv,x=x,y=y,radius=radius_of(lv),dropped=True)])
s = settle_merges(put(put(f0,3,-40,-450),3,40,-450)); assert _max_level(s)==4
s2 = settle_merges(put(put(f0,3,-200,-450),3,200,-450)); assert _max_level(s2)==3
print("  settle_merges: 相邻合成/远离不合成 ✓")
for name in PRESETS:
    t = PRESETS[name]
    f = settle_merges(mkfield(12, 3))
    t0=time.perf_counter(); choose_drop_x(f, 2, t, future_levels=[2,2,1]); dt=(time.perf_counter()-t0)*1000
    print(f"  预设 {name:<14} 决策 {dt:5.0f}ms")
print("\n✓ 全部通过")
