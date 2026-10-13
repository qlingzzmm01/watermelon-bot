# -*- coding: utf-8 -*-
"""付费防护自检：验证「涉及集结令/鱼翅/充值的一律不点」。

运行：python tools/test_paywall_guard.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guard  # noqa: E402

# ---------- 1. 危险词必须全部被拒 ----------
DANGEROUS = [
    "集结令", "招募令", "鱼翅", "充值", "购买", "支付", "付款",
    "兑换", "西瓜币兑换", "确认兑换", "确认购买", "立即购买",
    "送礼", "送出", "赠送", "送礼使用", "立即复活", "马上复活",
    "炸弹", "消除锤", "道具", "使用道具", "开通", "续费",
    "送出 集结令×2（2鱼翅）", "确认支付", "打赏",
]
# 允许的正常操作
SAFE = ["开始游戏", "合成大西瓜", "关闭", "取消", "返回"]

fails = []
print("=" * 62)
print("① 危险词拦截检查")
print("=" * 62)
for t in DANGEROUS:
    ok = guard.is_danger_text(t)
    mark = "✓ 已拦截" if ok else "✗ 漏放!"
    if not ok:
        fails.append(f"危险词未拦截: {t}")
    print(f"  {mark:10s} {t}")

print()
print("=" * 62)
print("② 正常操作放行检查（不得误伤）")
print("=" * 62)
for t in SAFE:
    d = guard.is_danger_text(t)
    mark = "✓ 放行" if not d else "✗ 误伤!"
    if d:
        fails.append(f"正常操作被误伤: {t}")
    print(f"  {mark:10s} {t}")

print()
print("=" * 62)
print("③ is_click_allowed 裁决检查")
print("=" * 62)
cases = [
    ("开始游戏", True),
    ("集结令", False),
    ("确认兑换", False),
    ("关闭", False),          # 非 extra_safe 时「关闭」不是游戏操作 → 拒
    ("关闭", True, True),     # extra_safe 时才放行
]
for c in cases:
    if len(c) == 3:
        t, expect, extra = c
        got, reason = guard.is_click_allowed(t, extra_safe=extra)
    else:
        t, expect = c
        got, reason = guard.is_click_allowed(t)
    mark = "✓" if got == expect else "✗"
    if got != expect:
        fails.append(f"裁决不符: {t} expect={expect} got={got}")
    print(f"  {mark} {t:10s} → allowed={got}  ({reason})")

print()
print("=" * 62)
print("④ 经济门闸检查")
print("=" * 62)
gate_cases = [
    (dict(coin_balance=0, free_left=0), False),
    (dict(coin_balance=0, free_left=2), True),
    (dict(coin_balance=100, free_left=0), True),
]
for kw, expect in gate_cases:
    got, reason = guard.economic_gate(None, **kw)
    mark = "✓" if got == expect else "✗"
    if got != expect:
        fails.append(f"门闸不符: {kw} expect={expect} got={got}")
    print(f"  {mark} {str(kw):40s} → allowed={got}")
    print(f"      {reason}")

print()
print("=" * 62)
if fails:
    print(f"❌ 失败 {len(fails)} 项：")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ 全部通过：涉及集结令/鱼翅/充值/兑换/送礼的目标一律不点。")
