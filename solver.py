# -*- coding: utf-8 -*-
"""
合成大西瓜落点决策器。

输入：游戏调试状态（__watermelonDebugState）
输出：最优投放 x 坐标（游戏坐标系）

坐标系：Cocos 2D，原点在场地水平中心，x 向右，y 向上。
  playWidth  场地宽度
  playBottom 地面 y
  playTop    场地顶部 y
  dropY      水果待落高度
  dangerY    死亡线 y
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace

# 水果等级 -> (半径, 合成得分)，来自游戏 assets/main 里解出的 oe 常量表
FRUITS = {
    1:  (26.0,  2),
    2:  (41.0,  4),
    3:  (54.0,  8),
    4:  (60.0,  16),
    5:  (76.0,  32),
    6:  (92.0,  64),
    7:  (97.0,  128),
    8:  (129.0, 256),
    9:  (154.0, 512),
    10: (164.0, 1024),
    11: (170.0, 2048),
}
MAX_LEVEL = 11

# WASM 规则：isWithinMergeDistance(dx, dy, r1, r2, 8) —— 判定容差 8
MERGE_TOLERANCE = 8.0


def radius_of(level: int) -> float:
    return FRUITS.get(level, FRUITS[1])[0]


def score_of(level: int) -> int:
    return FRUITS.get(level, FRUITS[1])[1]


@dataclass
class Fruit:
    id: int
    level: int
    x: float
    y: float
    radius: float
    dropped: bool
    merging: bool = False
    vx: float = 0.0
    vy: float = 0.0

    @property
    def speed(self) -> float:
        return abs(self.vx) + abs(self.vy)


@dataclass
class Field:
    play_width: float
    play_bottom: float
    play_top: float
    drop_y: float
    danger_y: float
    fruits: list = field(default_factory=list)

    @property
    def left(self) -> float:
        return -self.play_width / 2

    @property
    def right(self) -> float:
        return self.play_width / 2


@dataclass
class Tuning:
    """评分权重。数值经对局调参，可按手感微调。"""
    # ---- 基础项（旧预设沿用，行为不变）----
    merge_reward: float = 4000.0      # 触发一次合成的基准收益
    chain_bonus: float = 1.6          # 连锁合成的递增倍率
    height_penalty: float = 1.0       # 落点高度惩罚系数
    danger_penalty: float = 30000.0   # 越过死亡线惩罚
    surface_bonus: float = 0.9        # 贴合地形（不悬空）奖励
    big_on_small_penalty: float = 26.0  # 大水果压在小水果上的惩罚
    flat_bonus: float = 6.0           # 堆面平整奖励（降低高度方差）
    edge_penalty: float = 0.0         # 贴墙惩罚（默认关闭）

    # ---- 大西瓜（11 级）导向项 ----
    # 全部默认为 0 = 关闭，保持旧预设行为完全不变（向后兼容）。
    level_curve: float = 0.0           # >0 启用「等级进展」模型，替代游戏得分 2^n
    level_gain_scale: float = 120.0    # 等级进展价值的放大系数
    same_level_affinity: float = 0.0   # 同级靠近（未到合成距离）奖励：给序列下一颗铺路
    level_layering: float = 0.0        # 分层：大果在下、小果在上
    isolated_penalty: float = 0.0      # 孤立果惩罚：占空间且难再合成
    milestone_reward: float = 0.0      # 前瞻中「最高等级被推高」奖励（冲 11 核心）
    pair_boost: float = 1.0            # 序列配对感知：未来还有同类时亲和乘数（1.0=关）
    pair_decay: float = 1.0            # 未来无同类时亲和乘数（1.0=关）
    small_high_penalty: float = 1.0    # 分级高度惩罚：小果(≤3)堆高的惩罚倍率（>1 更狠）
    side_penalty: float = 0.0          # 半区平衡：落点半区比另一侧高出的惩罚系数（0=关）
    crowd_penalty: float = 0.0         # 方案A：低级果(≤4)落点周围密度惩罚（防挤堆，0=关）
    danger_ramp: float = 0.0           # 方案B：堆顶越过 dangerY-140 后的线性预警罚（0=关）
    # ---- v6 结构改进（wm-S）----
    pair_slot_reward: float = 0.0      # 配对槽位：落点若为低洼谷底（两侧表面更高）→奖励。
                                       # 作用：窗口预告 2×同级大果时，第 1 颗先进谷底预留，
                                       # 第 2 颗自然滚入合成——人类「挖坑等配对」动作，替代 v4 硬拽。
    pair_slot_scale: float = 2.2       # 谷底检测的邻域半宽（×半径）
    danger_curve: float = 0.0          # 指数死亡预警：y+radius 超过 dangerY-起始带后按距离²陡增
    danger_curve_start: float = 260.0  # 距死亡线多少 px 开始预警（260 ≈ 一个大果直径+余量）
    # ---- 架构方案 2026-09-05 ----
    robust_amp: float = 0.0            # 方案①鲁棒落点：邻域扰动幅度 px（0=关）。
                                       # >0 时对每个候选做 ±amp/2 三点模拟，取平均评分——
                                       # 对抗 simulate_drop ~133px 真实误差：选「宽谷/平底」鲁棒位
                                       # 而非只对单个模拟点最优（模拟偏一点就掉进别处）的窄尖峰。
    beam_width: int = 1                # 方案②滚动时域前瞻：每层保留的路径数（1=贪心=现状）。
                                       # >1 时对未来序列做 beam search：不再每步只取单个最优落点，
                                       # 而是保留前 beam_width 个布局分头演化——能发现「暂时少合一步、
                                       # 但两步后促成大合成」的全局更优路径（v1 死亡常因贪心短视）。
    beam_depth: int = 0                # beam 前瞻深度（0=用满 future_levels 窗口）
    dmode: float = 0.0                 # 方案③数据节奏自适应（bot 层读取）：本局合成进度落后于
                                       # 历史健康线时，切「清小果保命」模式（见 bot._loop），0=关

    samples: int = 96                 # 候选落点采样数
    sim_step: float = 3.0             # 下落模拟步长
    sim_max_iter: int = 900
    roll_factor: float = 0.85         # 碰撞侧向滑移系数（0=不滚；真实物理实测校准用）


# ===== 预设方案（2026-09-06 精简：仅保留 2 个可切换策略）=====
# default「默认」= 当前实证最优：v1 参数 + 鲁棒落点 robust_amp=45（wm-R 固化）。
#   15 局对照中唯一出西瓜的配置（lv11/lv10/lv10/lv9/lv10 = 平均10.0 / 1西瓜）。
# default2「默认2」= 纯 v1（无鲁棒落点）：v1(affinity1600/height4) 3 局 lv10/lv10/lv11。
# 历史对照（勿再臆测新权重）：v2(affinity5000)9.3/0、v3(height7)9.0/0、
#   wm-H(Beam)9.6/0、wm-D(数据节奏)9.4/0 —— 均已删除。
PRESETS = {
    "default": Tuning(
        merge_reward=12000.0,
        chain_bonus=2.0,
        level_curve=2.6,             # 等级进展模型（替代 2^n 得分，保留低等级铺垫价值）
        level_gain_scale=120.0,
        same_level_affinity=1600.0,  # 同级靠近铺路（序列驱动核心）
        level_layering=900.0,        # 大果在下、小果在上
        isolated_penalty=700.0,      # 孤立大果惩罚
        milestone_reward=9000.0,     # 前瞻里推高最高等级的奖励
        robust_amp=45.0,             # 鲁棒落点：±22px 三点扰动取平均评分（抗模拟误差）
        height_penalty=4.0,          # 压堆高
        danger_penalty=70000.0,
        surface_bonus=1.5,
        big_on_small_penalty=80.0,   # 反对大压小（破坏分层）
        flat_bonus=16.0,
        edge_penalty=5.0,
        samples=160,                 # 两阶段粗搜+细化采样
        sim_max_iter=1200,
    ),
    "default2": Tuning(
        merge_reward=12000.0,
        chain_bonus=2.0,
        level_curve=2.6,
        level_gain_scale=120.0,
        same_level_affinity=1600.0,
        level_layering=900.0,
        isolated_penalty=700.0,
        milestone_reward=9000.0,
        robust_amp=0.0,              # 纯 v1：不做三点扰动，直接取模拟单点最优
        height_penalty=4.0,
        danger_penalty=70000.0,
        surface_bonus=1.5,
        big_on_small_penalty=80.0,
        flat_bonus=16.0,
        edge_penalty=5.0,
        samples=160,
        sim_max_iter=1200,
    ),
}


def simulate_drop(field: Field, start_x: float, radius: float,
                  tuning: Tuning) -> tuple:
    """
    模拟水果从 drop_y 垂直落下，返回静止时的 (x, y)。

    用「逐步下落 + 圆-圆分离 + 侧向滑移」近似 Box2D 结果：
    每步先下降，再把与已有水果的重叠沿法线推出，同时允许横向滑动（模拟滚落）。

    性能（实测占决策耗时 99%，已针对性优化）：
      1) 距离判定用平方比较，只在真正重叠时才开方——省掉绝大部分 hypot/sqrt。
      2) 自适应步长：下落方向无遮挡时用大步快进，接近障碍再恢复细步。
      3) 分离迭代 4 → 2 次（实测落点差异 < 1px）。
    """
    x = start_x
    y = field.drop_y
    half = field.play_width / 2
    lo, hi = -half + radius, half - radius
    bottom = field.play_bottom + radius
    step0 = tuning.sim_step

    solids = [(f.x, f.y, f.radius) for f in field.fruits if f.dropped and not f.merging]

    it = 0
    no_move_steps = 0
    prev_x, prev_y = x, y
    while it < tuning.sim_max_iter:
        it += 1

        # 静止检测（要够严，避免水果停在斜坡暂态点）：
        # 真实物理里圆形水果在斜坡上是不稳定平衡，会继续滚到谷底。
        # 只有「连续多步几乎完全不动」才允许提前停；并且迭代数太小时不判，
        # 防止刚接触就把水果钉在表面上。
        if it > 60:
            if abs(x - prev_x) + abs(y - prev_y) < 0.02:
                no_move_steps += 1
                if no_move_steps >= 12:
                    break
            else:
                no_move_steps = 0
        prev_x, prev_y = x, y

        # 自适应步长：求下落路径上最近的障碍间隙（到地面或到下方水果表面）
        gap = y - bottom
        for fx, fy, fr in solids:
            dx = x - fx
            need = radius + fr
            if dx > need or dx < -need:
                continue                      # x 方向不可能接触，直接跳过
            dy = y - fy
            if dy <= 0.0:
                continue                      # 只关心下方的水果
            d2 = dx * dx + dy * dy
            if d2 >= need * need:
                # 未接触：垂直间隙 = dy - sqrt(need² - dx²)
                gap_i = dy - math.sqrt(need * need - dx * dx)
            else:
                gap_i = 0.0
            if gap_i < gap:
                gap = gap_i
        if gap < 0.0:
            gap = 0.0

        step = step0 if gap < step0 * 3.0 else min(gap * 0.5, step0 * 6.0)
        if step < step0:
            step = step0
        y -= step
        if y <= bottom:
            y = bottom
            break

        # 与已有水果分离 + 滑移
        for _ in range(2):
            overlapped = False
            for fx, fy, fr in solids:
                dx, dy = x - fx, y - fy
                need = radius + fr
                d2 = dx * dx + dy * dy
                if d2 >= need * need:
                    continue                  # 平方比较，避免开方
                overlapped = True
                d = math.sqrt(d2)
                if d < 1e-6:
                    dx, dy, d = 0.0, 1.0, 1e-6
                push = need - d
                nx, ny = dx / d, dy / d
                x += nx * push
                y += ny * push
                # 侧向滑移：接触点越偏，横向滚落越多（系数 roll_factor 可校准）
                slide = abs(nx) * push * tuning.roll_factor
                x += math.copysign(slide, nx) if nx != 0 else slide
            if x < lo:
                x = lo
            elif x > hi:
                x = hi
            if not overlapped:
                break

    return x, max(y, bottom)


def level_progress(level: int, tuning: Tuning) -> float:
    """合成到 level+1 的「等级进展价值」。

    level_curve > 0 时用 (level+1)^curve * scale，而不是游戏的 2^n 得分表。
    差别：2^n 下 lv10→11(2048) 是 lv1→2(4) 的 512 倍，决策会为一次高分成
    彻底牺牲布局；幂函数下只有约 5.6 倍——高等级仍是目标，但低等级铺垫
    也保留价值（没有低等级合成就堆不到 11）。
    """
    if level >= MAX_LEVEL:
        return 0.0
    if tuning.level_curve > 0:
        return float(level + 1) ** tuning.level_curve * tuning.level_gain_scale
    return float(score_of(level + 1))


def merge_gain(level: int, depth: int = 0, tuning: Tuning | None = None) -> float:
    """合成到 level+1 的收益，depth 表示连锁层数。"""
    if level >= MAX_LEVEL:
        return 0.0
    base = level_progress(level, tuning or Tuning())
    return base * (1.0 + 0.35 * depth)


def _same_level_affinity(field: Field, level: int, x: float, y: float,
                         radius: float, tuning: Tuning) -> float:
    """落点与最近同级水果的聚集奖励（尚未达到合成距离时）。

    序列驱动的核心：若抓取到的序列下一颗还是同 level，靠在一起摆 →
    下一颗落下时直接合成。距离越近奖励越高，衰减到 70px 外几乎为零。
    """
    if tuning.same_level_affinity <= 0:
        return 0.0
    best = None
    for f in field.fruits:
        if not f.dropped or f.merging or f.level != level:
            continue
        d = math.hypot(x - f.x, y - f.y) - (radius + f.radius)
        if d >= MERGE_TOLERANCE and (best is None or d < best):
            best = d
    if best is None:
        return 0.0
    return tuning.same_level_affinity / (1.0 + best / 70.0)


def _pair_slot_bonus(field: Field, x: float, y: float, radius: float,
                     tuning: Tuning) -> float:
    """配对槽位奖励（v6 结构性改进）：落点若是「低洼谷底」→ 奖励。

    动机：窗口预告 2×同级大果（如 2×lv9）时，人类不是把第 1 颗硬塞到已有果旁
    （v4 亲和×2.5 的错误：直接合成消耗 → 单极；或堆高塔），而是把第 1 颗放进
    一个低洼开阔的谷底「预留」，第 2 颗同类自然滚入谷底与之合成。

    判定：以落点为中心，左右 ±1.6r 邻域地表都比落点高 → 形成 V 槽，是天然汇聚点。
    """
    if tuning.pair_slot_reward <= 0:
        return 0.0
    half = field.play_width / 2
    # 探针半宽：1.5r（2.2r 对大果会探出场外 → 过滤丢奖励）。越界贴墙 clamp 而非丢弃。
    w = min(1.5 * radius, tuning.pair_slot_scale * radius)   # 槽宽尺度
    probe_xs = []
    for sgn in (-1.0, 1.0):
        px = x + sgn * w
        probe_xs.append(max(-half + 1.0, min(half - 1.0, px)))
    wall_top = []
    for px in probe_xs:
        # px 处地表最高点（任何已落果的表面）
        top = field.play_bottom
        for f in field.fruits:
            if not f.dropped or f.merging:
                continue
            if abs(f.x - px) <= radius + f.radius:
                top = max(top, f.y + f.radius)
        wall_top.append(top)
    # 两侧都明显高于落点底部 → V 槽。谷越深奖励越大
    base = y - radius                       # 落点底部
    depth_l = max(0.0, wall_top[0] - base)
    depth_r = max(0.0, wall_top[1] - base)
    if depth_l > 4.0 and depth_r > 4.0:
        return tuning.pair_slot_reward * (1.0 + (depth_l + depth_r) / 200.0)
    return 0.0


def _level_layering_penalty(field: Field, level: int, x: float, y: float,
                            radius: float, tuning: Tuning) -> float:
    """分层惩罚（返回正值，越大越差）：大果应在下、小果应在上。

    大果悬在高处会占掉小果的落位空间，也会让表面凹凸——是堆不到 11 级的
    主要原因之一。
    """
    if tuning.level_layering <= 0:
        return 0.0
    pen = 0.0
    for f in field.fruits:
        if not f.dropped or f.merging:
            continue
        if level > f.level and y > f.y + radius:
            pen += tuning.level_layering * (level - f.level) * 0.4    # 大果在上：重罚
        elif level < f.level and y + radius < f.y:
            pen += tuning.level_layering * (f.level - level) * 0.12   # 小果被压：轻罚
    return pen


def _isolated_penalty(field: Field, level: int, x: float, y: float,
                      radius: float, tuning: Tuning) -> float:
    """孤立果惩罚：新落点周围没有同级 → 这颗变成死重，占空间又难再合成。

    等级越高越亏（大果孤零零地躺场上，几乎不可能再等到同级）。
    """
    if tuning.isolated_penalty <= 0:
        return 0.0
    dmin = None
    for f in field.fruits:
        if not f.dropped or f.merging or f.level != level:
            continue
        d = math.hypot(x - f.x, y - f.y) - (radius + f.radius)
        if d <= MERGE_TOLERANCE:
            return 0.0          # 能合成，不罚
        if dmin is None or d < dmin:
            dmin = d
    weight = (level / MAX_LEVEL) ** 2
    if dmin is None:
        return tuning.isolated_penalty * weight
    return tuning.isolated_penalty * weight / (1.0 + dmin / 120.0)


def evaluate(field: Field, level: int, x: float, y: float,
             radius: float, tuning: Tuning) -> float:
    """评估落点 (x, y) 的分数，越高越好。"""
    s = 0.0

    # 1) 合成收益（含连锁预演）
    same = [f for f in field.fruits
            if f.dropped and not f.merging and f.level == level]
    merged_now = False
    for f in same:
        d = math.hypot(x - f.x, y - f.y) - (radius + f.radius)
        if d <= MERGE_TOLERANCE:
            s += tuning.merge_reward + merge_gain(level, 0, tuning)
            merged_now = True
            # 连锁：合成后的新水果若又能与同级接触
            nl = level + 1
            if nl <= MAX_LEVEL:
                nr = radius_of(nl)
                mx, my = (x + f.x) / 2, (y + f.y) / 2
                for g in field.fruits:
                    if g.id == f.id or not g.dropped or g.merging or g.level != nl:
                        continue
                    if math.hypot(mx - g.x, my - g.y) - (nr + g.radius) <= MERGE_TOLERANCE:
                        s += tuning.merge_reward * (tuning.chain_bonus ** 1) * 0.5
                        break
            break

    # 2) 大西瓜导向项（仅在对应权重 > 0 时生效）
    if not merged_now:
        s += _same_level_affinity(field, level, x, y, radius, tuning)
        s -= _isolated_penalty(field, level, x, y, radius, tuning)
    s -= _level_layering_penalty(field, level, x, y, radius, tuning)

    # 3) 高度惩罚（越低越好）。分级：小果(≤3)堆高纯浪费空间，重罚；大果(≥6)是成果容忍高位
    h_mult = tuning.small_high_penalty if level <= 3 else 1.0
    s -= (y - field.play_bottom) * tuning.height_penalty * h_mult

    # 3) 死亡线
    if y + radius > field.danger_y:
        s -= tuning.danger_penalty
        over = (y + radius - field.danger_y)
        s -= over * 60.0
    # 3b) 高位预警（方案B）：堆顶逼近死亡线前线性加压，保命优先但不动摇低处合成节奏
    if tuning.danger_ramp > 0:
        ramp_over = (y + radius) - (field.danger_y - 140.0)
        if ramp_over > 0:
            s -= ramp_over * tuning.danger_ramp
    # 3b2) 指数死亡预警（wm-S 结构改进，替代 v3/B 的线性全程压高）：
    #     距死亡线 danger_curve_start 内才开始按 距离² 陡增惩罚——平时零负担不拖节奏
    #     （v3 height7 全程压高→卡 lv9；B 线性 140px 预警→保守），只在真正逼近死亡时才强拒。
    if tuning.danger_curve > 0:
        over2 = (y + radius) - (field.danger_y - tuning.danger_curve_start)
        if over2 > 0:
            s -= tuning.danger_curve * over2 * over2 / 100.0

    # 3c) 配对槽位（wm-S 结构改进）：落点是低洼谷底 → 奖励。
    #     窗口预告 2×同级大果时把第 1 颗放进谷底预留，第 2 颗滚入合成——
    #     人类「挖坑等配对」，替代 v4 亲和×2.5 的硬靠（其缺陷：直接合成单极消耗/堆高塔）。
    #     仅对 level≥5 且未直接合成的落点生效（小果直接合成即可，无需挖坑预留）。
    if tuning.pair_slot_reward > 0 and level >= 5 and not merged_now:
        s += _pair_slot_bonus(field, x, y, radius, tuning)

    # 4) 贴合地形：落点周围应紧邻支撑，避免悬空塔
    support = _support_depth(field, x, y, radius)
    s -= support * tuning.surface_bonus * 10.0

    # 5) 大水果压小水果惩罚
    for f in field.fruits:
        if not f.dropped or f.merging:
            continue
        if f.y < y and abs(f.x - x) < radius + f.radius:
            if level > f.level:
                s -= tuning.big_on_small_penalty * (level - f.level)

    # 6) 平整度：落点后 overall 高度方差
    s -= _roughness(field, x, y, radius) * tuning.flat_bonus

    # 7) 半区平衡：落点所在半区（左/右）比另一侧高太多 → 罚（死亡局多为单侧堆到顶）
    if tuning.side_penalty > 0:
        lt = ot = field.play_bottom
        for f in field.fruits:
            if not f.dropped or f.merging:
                continue
            top = f.y + (f.radius or 0)
            if f.x < 0:
                lt = max(lt, top)
            else:
                ot = max(ot, top)
        my_side = lt if x < 0 else ot
        other = ot if x < 0 else lt
        s -= tuning.side_penalty * max(0.0, my_side - other - 80.0)

    # 8) 拥挤度（方案A）：低级果落点周围果密度太高 → 罚，引导低级果散开成对放、
    #    避免挤成死堆（死亡局 finalLayout 常见 lv1-4 成片积压占位）
    if tuning.crowd_penalty > 0 and level <= 4:
        cnt = sum(1 for f in field.fruits
                  if f.dropped and not f.merging and abs(f.x - x) < 150.0)
        if cnt > 4:
            s -= tuning.crowd_penalty * (cnt - 4)

    return s


def _support_depth(field: Field, x: float, y: float, radius: float) -> float:
    """落点下方最近的支撑面距离，越小越贴合。"""
    best = y - radius - field.play_bottom
    for f in field.fruits:
        if not f.dropped or f.merging:
            continue
        if abs(f.x - x) <= radius + f.radius and f.y < y:
            gap = (y - radius) - (f.y + f.radius)
            if -radius < gap < best:
                best = max(gap, 0.0)
    return max(best, 0.0)


def _roughness(field: Field, x: float, y: float, radius: float) -> float:
    """加入新水果后，场地表面高度的方差（越小越平整）。"""
    cols = 16
    half = field.play_width / 2
    top = [field.play_bottom] * cols
    pts = [(f.x, f.y + f.radius) for f in field.fruits if f.dropped and not f.merging]
    pts.append((x, y + radius))
    for px, py in pts:
        i = int((px + half) / field.play_width * cols)
        i = max(0, min(cols - 1, i))
        if py > top[i]:
            top[i] = py
    mean = sum(top) / cols
    return math.sqrt(sum((t - mean) ** 2 for t in top) / cols)


# 前瞻模拟用的临时水果 id（负值避免与真实 id 冲突）
_PLACE_ID = [0]


def _next_id() -> int:
    _PLACE_ID[0] -= 1
    return _PLACE_ID[0]


def _clone_field(field: Field, fruits: list) -> Field:
    return Field(field.play_width, field.play_bottom, field.play_top,
                 field.drop_y, field.danger_y, fruits)


def _max_level(field: Field) -> int:
    """场上已落水果的最高等级（0 表示空场）。"""
    return max([f.level for f in field.fruits if f.dropped and not f.merging] + [0])


def settle_merges(field: Field, max_rounds: int = 6,
                  tolerance: float | None = None) -> Field:
    """把场上互相接触的同级水果两两合并（真正执行合成），反复直到稳定。

    旧的前瞻只「加一次合成分」但水果仍在场上，误差会随深度累积——
    预测不出「再投 3 颗到底能堆到几级」。这里按游戏规则真实结算：
    同级接触 → 合成为 level+1，位置取中点，11 级封顶。
    """
    tol = MERGE_TOLERANCE if tolerance is None else tolerance
    fruits = list(field.fruits)
    for _ in range(max_rounds):
        out = []
        used = [False] * len(fruits)
        merged_any = False
        for i, a in enumerate(fruits):
            if used[i]:
                continue
            hit = -1
            for j in range(i + 1, len(fruits)):
                if used[j]:
                    continue
                b = fruits[j]
                if b.level != a.level or a.level >= MAX_LEVEL:
                    continue
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d - (a.radius + b.radius) <= tol:
                    hit = j
                    break
            if hit >= 0:
                b = fruits[hit]
                used[i] = used[hit] = True
                nl = min(a.level + 1, MAX_LEVEL)
                out.append(Fruit(id=_next_id(), level=nl,
                                 x=(a.x + b.x) / 2, y=(a.y + b.y) / 2,
                                 radius=radius_of(nl), dropped=True))
                merged_any = True
            else:
                used[i] = True
                out.append(a)
        fruits = out
        if not merged_any:
            break
    return _clone_field(field, fruits)


def _place_fruit(field: Field, level: int, x: float, y: float, radius: float) -> Field:
    """返回加入一颗已落水果后的新 Field（不修改原场）。"""
    nf = Fruit(id=_next_id(), level=level, x=x, y=y,
               radius=radius, dropped=True)
    return _clone_field(field, field.fruits + [nf])


def _score_drop(field: Field, cx: float, level: int, radius: float,
                tuning: Tuning, settle: bool = False):
    """模拟在 cx 投放并评分，返回 (评分, 代表静止x, 代表静止y, 结算后Field)。

    robust_amp>0（方案①wm-R）：对 cx 及 ±amp/2 三点各做一次模拟，
    评分取加权平均（中心权重 2）。目的：对抗 simulate_drop ~133px 真实误差——
    只对单个模拟点最优的「窄尖峰」落点，真实投放偏一点就掉进别处；而宽谷/平底
    落点在邻域扰动下评分稳定，才是可信决策点。
    """
    amp = tuning.robust_amp
    if amp <= 0:
        fx, fy = simulate_drop(field, cx, radius, tuning)
        sc = evaluate(field, level, fx, fy, radius, tuning)
        nf = None
        if settle:
            nf = _place_fruit(field, level, fx, fy, radius)
            nf = settle_merges(nf, max_rounds=4)
        return sc, fx, fy, nf
    half_a = amp / 2.0
    lo = -field.play_width / 2 + radius
    hi = field.play_width / 2 - radius
    acc, wsum = 0.0, 0.0
    rx, ry = cx, field.play_bottom
    for dx, w in ((0.0, 2.0), (-half_a, 1.0), (half_a, 1.0)):
        x2 = max(lo, min(hi, cx + dx))
        fx, fy = simulate_drop(field, x2, radius, tuning)
        acc += w * evaluate(field, level, fx, fy, radius, tuning)
        wsum += w
        if dx == 0.0:
            rx, ry = fx, fy
    nf = None
    if settle:
        nf = _place_fruit(field, level, rx, ry, radius)
        nf = settle_merges(nf, max_rounds=4)
    return acc / wsum, rx, ry, nf


def _future_chain_score(field: Field, levels, tuning: Tuning,
                        coarse: int = 5, settle: bool = True) -> float:
    """按抓取到的序列推演布局，返回累计评分（贪心或 beam，由 beam_width 决定）。

    旧版（beam_width=1）：每步只取单个最优落点贪心演化 → 短视：为了一次小合成
    牺牲两步后的更大合成，正是 v1 死亡局「攒不出第二个 lv10」的机理。
    方案②wm-H（beam_width>1）：每层保留前 beam_width 条布局分头演化，能发现
    「暂时少合一步、但两步后促成大合成」的全局更优路径。

    未来模拟用加倍步长提速（前瞻只看趋势，不需高精度）。
    """
    fast = replace(tuning, sim_step=tuning.sim_step * 2.0)
    bw = max(1, int(tuning.beam_width))
    beam = [(field, 0.0, _max_level(field))]        # (field, 累计分, 曾达最高级)
    for lv in levels:
        r = radius_of(lv)
        half = beam[0][0].play_width / 2
        lo, hi = -half + r, half - r
        if hi <= lo:
            break
        n_cand = max(2, coarse)
        new_beam = []
        for f0, s0, pmax in beam:
            prev_max = _max_level(f0)
            best_local = None
            for i in range(n_cand):
                cx = lo + (hi - lo) * i / (n_cand - 1)
                sc, fx, fy, nf = _score_drop(f0, cx, lv, r, fast, settle=True)
                cur_max = max(prev_max, _max_level(nf))
                bonus = 0.0
                if tuning.milestone_reward > 0 and cur_max > prev_max:
                    bonus = tuning.milestone_reward * (cur_max - prev_max) * (cur_max / MAX_LEVEL)
                new_beam.append((nf, s0 + sc + bonus, max(pmax, cur_max)))
        # 保留全局 top beam_width 条路径
        new_beam.sort(key=lambda t: t[1], reverse=True)
        beam = new_beam[:bw]
    return beam[0][1]


def choose_drop_x(field: Field, level: int, tuning: Tuning | None = None,
                  future_levels: list | None = None,
                  future_depth: int = 2) -> float:
    """
    返回最优投放 x（游戏坐标系）。

    策略：
      1. 在场地宽度上等距采样候选落点
      2. 对每个落点做下落模拟
      3. 用评分函数选最优

    前瞻（future_levels 非空时）：
      先取基础评分 Top K，再对每个候选依次模拟「接下来 N 颗水果」
      的粗粒度贪心落点，用最终累计评分修正决策——让落点对后续序列更有利。

    性能自适应：场上水果越多，采样与前瞻越少（保证决策在投放间隔内完成）。
    """
    tuning = tuning or Tuning()
    # 序列配对感知（v4 结构性改进，由 pair_boost/pair_decay 开关，1.0=关闭）：
    # 看未来 6 颗里还有几个与当前同等级。
    #   peer>=1（近期还会来同类）→ 亲和 ×pair_boost(≈2.5)：放到已有同伴旁，下一颗同类来了
    #       就能直接合成——「每局稳定出一个大西瓜」的核心动作。
    #   peer==0（这类果近期不再来）→ 亲和 ×pair_decay(≈0.4)：别执着靠拢（靠拢也无法配对），
    #       放低处保命，避免孤立大果占高空间变成死重。
    if future_levels and tuning.same_level_affinity > 0 and tuning.pair_boost != 1.0:
        peer = sum(1 for lv in future_levels[:6] if lv == level)
        if peer >= 1:
            tuning = replace(tuning, same_level_affinity=tuning.same_level_affinity * tuning.pair_boost)
        else:
            tuning = replace(tuning, same_level_affinity=tuning.same_level_affinity * tuning.pair_decay)
    # 配对槽位动态开关（wm-S 结构改进，pair_slot_reward>0 时启用）：
    #   未来窗口(6 颗)里还有 ≥1 颗同类 → 本颗扮演「第 1 颗」，放进低洼谷底等第 2 颗滚入合成
    #   （挖坑等配对，替代 v4 硬靠）。未来无同类 → 关闭槽位奖励，保持 v1 行为（避免孤立果全堆谷底）。
    if tuning.pair_slot_reward > 0:
        if future_levels and any(lv == level for lv in future_levels[:6]):
            tuning = tuning   # 保持开启
        else:
            tuning = replace(tuning, pair_slot_reward=0.0)
    radius = radius_of(level)
    half = field.play_width / 2
    lo, hi = -half + radius, half - radius
    if hi <= lo:
        return 0.0

    t0 = time.perf_counter()
    fcount = len([f for f in field.fruits if f.dropped and not f.merging])
    # 按场上果数调整：采样数 / 前瞻深度 / Top K / 粗候选数
    # 抓到序列时前瞻是「冲 11 级」的核心手段，尽量保留深度（旧版果多时直接降为 0）
    # simulate_drop 优化后单次决策仅约 30ms（投放间隔 550ms），
    # 因此序列可用时能放开前瞻深度——深度是「按序列预判冲 11 级」的关键。
    have_seq = bool(future_levels)
    if fcount > 24:
        n = max(16, tuning.samples // 3)
        depth, top_k, coarse = (2 if have_seq else 0), 8, 4
    elif fcount > 12:
        n = max(16, tuning.samples // 2)
        depth, top_k, coarse = (3 if have_seq else 1), 10, 4
    else:
        n = max(16, tuning.samples)
        depth, top_k, coarse = min(future_depth, 4), 12, 5

    # 两阶段采样：先在全场粗搜，再对最优的几个点做局部细化。
    # 比单纯提高均匀采样点数更快，且不漏掉窄缝里的好落点。
    # 方案①(wm-R robust_amp>0)：_score_drop 内部做 ±amp/2 三点扰动取平均评分。
    n_coarse = max(12, n // 3)
    ranked = []
    for i in range(n_coarse):
        cx = lo + (hi - lo) * i / (n_coarse - 1)
        sc, fx, fy, _ = _score_drop(field, cx, level, radius, tuning)
        ranked.append((sc, cx, fx, fy))
    ranked.sort(key=lambda t: t[0], reverse=True)

    # 细化：在 Top3 附近按 1/4 粗间隔补点（越靠近最优，补得越密）
    step = (hi - lo) / max(1, n_coarse - 1)
    for sc0, cx0, _, _ in ranked[:3]:
        for k in (-2, -1, 1, 2):
            cx = cx0 + step * k / 4.0
            if cx < lo or cx > hi:
                continue
            sc, fx, fy, _ = _score_drop(field, cx, level, radius, tuning)
            ranked.append((sc, cx, fx, fy))
    ranked.sort(key=lambda t: t[0], reverse=True)

    future = (future_levels or [])[:depth] if future_levels else []
    if not future:
        return ranked[0][1]

    # 当前这颗先做一次真合成结算，再交给序列前瞻——否则前瞻起点就带误差
    # 决策预算：投放间隔约 550ms，留足余量，超时就用已算出的最优（宁可少看几步）
    budget = 0.45
    best_x, best_s = ranked[0][1], -float("inf")
    for k, (sc0, cx, fx, fy) in enumerate(ranked[:top_k]):
        if k > 0 and time.perf_counter() - t0 > budget:
            break
        f1 = _place_fruit(field, level, fx, fy, radius)
        f1 = settle_merges(f1, max_rounds=3)
        tot = sc0 + _future_chain_score(f1, future, tuning, coarse=coarse)
        if tot > best_s:
            best_s, best_x = tot, cx
    return best_x


def parse_debug_state(state: dict) -> tuple:
    """把 __watermelonDebugState 解析成 (Field, current_level, is_settled)。"""
    layout = state.get("layout") or {}
    fruits = []
    for f in state.get("fruits") or []:
        fruits.append(Fruit(
            id=f.get("id"), level=f.get("level", 1),
            x=f.get("x", 0.0), y=f.get("y", 0.0),
            radius=f.get("radius") or radius_of(f.get("level", 1)),
            dropped=bool(f.get("dropped")),
            merging=bool(f.get("merging")),
            vx=f.get("vx", 0.0), vy=f.get("vy", 0.0),
        ))
    field = Field(
        play_width=layout.get("playWidth", 700.0),
        play_bottom=layout.get("playBottom", -500.0),
        play_top=layout.get("playTop", 500.0),
        drop_y=layout.get("dropY", 500.0),
        danger_y=layout.get("dangerY", 440.0),
        fruits=fruits,
    )

    cur_id = state.get("currentFruitId")
    cur_level = None
    if cur_id is not None:
        for f in fruits:
            if f.id == cur_id:
                cur_level = f.level
                break

    # 静止判定：无正在合并、无正在下落、所有已落水果速度足够小
    moving = [f for f in fruits if f.dropped and not f.merging and f.speed > 12.0]
    settled = (not moving) and state.get("droppingFruitId") is None

    return field, cur_level, settled
