# -*- coding: utf-8 -*-
"""
付费弹层哨兵（Paywall Sentinel）—— 保证 bot 永不消耗鱼翅。

设计原则（2026-10-13 用户明确要求）：
    只要涉及「集结令 / 鱼翅 / 充值 / 兑换 / 送礼」的按钮，一律不点；
    涉及需要充值的一律不点。宁可停下等人工，也绝不触发扣费。

背景（逆向确认）：
  - 「集结令」= 斗鱼付费礼物 giftId=24943，1 鱼翅/枚。
  - 出现位置：
      ① 对局中工具栏「炸弹 / 复活道具」→ 送礼使用（集结令×1 / ×2）
      ② 死亡弹窗「立即复活」（集结令×2）
      ③ 「西瓜币兑换」底部面板（集结令×1→10币 … ×500→6500）
  - **致命自动路径**：点「开始游戏」但西瓜币不足 → 服务端返回 1043002 →
    游戏自动调用 openCoinExchange() 弹出兑换面板；此时 bot 的坐标盲点兜底
    可能凑出「选礼包 + 点确认」→ 真扣鱼翅。

本模块提供：
    Sentinel            —— 在 page 上检测弹层、只关闭不确认
    DANGER_WORDS        —— 危险词表（任何点击目标命中即拒绝）
    is_click_allowed()  —— 点击白名单/黑名单裁决
    ClickAudit          —— 点击黑匣子日志（落盘 logs/click_audit.log）
"""
from __future__ import annotations

import json
import os
import re
import time

# ---------------------------------------------------------------- 危险词
# 任何点击目标（按钮文字 / DOM 文本 / 节点名）命中以下任一词 → 一律拒绝点击。
# 注意：这里只做「拒绝」，不做「点击」。宁可漏点，不可错点。
DANGER_WORDS = (
    # 付费礼物
    "集结令", "招募令", "鱼翅", "鱼丸", "礼物",
    # 充值 / 购买
    "充值", "购买", "支付", "付款", "下单", "开通", "续费", "支付方式",
    # 兑换 / 消耗
    "兑换", "换取", "消费", "扣费", "花费", "使用道具", "购买道具",
    # 送礼动作
    "送礼", "送出", "赠送", "打赏", "赠送礼物",
    # 复活 / 道具付费入口（道具本身可能免费，但无库存时会转送礼，故一律不点）
    "立即复活", "马上复活", "道具", "炸弹", "消除锤",
    # 通用确认（可能触发扣费）
    "确认购买", "确认兑换", "确认支付", "确定购买", "立即购买", "立即兑换",
    "立即开通", "确认送出", "确认送礼",
)

# 只允许点击的「关闭类」词（弹层出现时唯一允许的动作）
SAFE_CLOSE_WORDS = (
    "关闭", "取消", "返回", "×", "✕", "✖", "╳", "x",
)

# 点击目标必须包含其中之一（即「这是一个正常游戏操作」）才允许盲点兜底
GAME_ACTION_WORDS = (
    "开始游戏", "开始", "合成大西瓜",
)


def is_danger_text(text: str) -> bool:
    """文本是否命中危险词（付费/充值/兑换/送礼）。"""
    if not text:
        return False
    t = re.sub(r"\s+", "", str(text))
    return any(w in t for w in DANGER_WORDS)


def is_click_allowed(text: str, extra_safe: bool = False) -> tuple[bool, str]:
    """裁决一次点击是否允许。

    Returns: (allowed, reason)
    """
    t = re.sub(r"\s+", "", str(text or ""))
    if not t:
        return False, "空目标"
    if is_danger_text(t):
        return False, "命中付费危险词"
    if extra_safe:
        return True, "弹层关闭动作"
    if any(w in t for w in GAME_ACTION_WORDS):
        return True, "正常游戏操作"
    return False, "非白名单目标"


# ---------------------------------------------------------------- 哨兵 JS
# 检测页面上的付费弹层（React 外层 DOM + Cocos canvas 内弹层）。
# 只做「检测」，不做任何点击。
SCAN_PAYWALL_JS = r"""
() => {
  const out = {hit: false, kinds: [], dom: [], cocos: []};
  const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;

  // ---- ① React 外层弹层（wrapper 的 CoinExchangeModal / SendGiftModal）----
  try {
    const clsOf = e => (e.className || '').toString();
    document.querySelectorAll('[class*="Modal-module"], [class*="modal"]').forEach(e => {
      const c = clsOf(e);
      if (/CoinExchangeModal/i.test(c))  { out.kinds.push('coin-exchange'); out.dom.push(c.slice(0,80)); }
      else if (/SendGiftModal/i.test(c)) { out.kinds.push('send-gift');     out.dom.push(c.slice(0,80)); }
      else if (/Revive/i.test(c) && /Modal|Dialog/i.test(c)) {
        out.kinds.push('revive'); out.dom.push(c.slice(0,80));
      }
    });
    // 文案兜底：页面上出现「西瓜币兑换 / 送出 集结令」等字样
    const bodyTxt = (document.body && document.body.innerText) || '';
    if (/西瓜币兑换|兑换西瓜币|送出\s*集结令|集结令\s*×/.test(bodyTxt)) {
      out.kinds.push('paywall-text');
    }
  } catch (e) {}

  // ---- ② Cocos canvas 内弹层（复活 / 道具 / 结算确认）----
  try {
    const cc = window.cc || window.cclegacy;
    const scene = cc && cc.director && cc.director.getScene && cc.director.getScene();
    if (scene) {
      const NAMES = ['ReviveDialogLayer','ReviveDialog','ToolDialogLayer','ToolDialog',
                     'EndGameConfirmLayer','EndGameDialog','LotteryTenDrawConfirmLayer',
                     'GameErrorDialogLayer','CoinExchangeLayer','GiftDialogLayer'];
      const stack = [scene];
      while (stack.length) {
        const n = stack.pop();
        if (!n) continue;
        const nm = n.name || '';
        if (NAMES.some(k => nm.indexOf(k) >= 0)) {
          // 节点链必须全部激活才算「正在显示」
          let p = n, active = true;
          while (p) { if (p.active === false) { active = false; break; } p = p.parent; }
          if (active) { out.cocos.push(nm); out.kinds.push('cocos:' + nm); }
        }
        for (const ch of (n.children || [])) stack.push(ch);
      }
    }
  } catch (e) {}

  out.kinds = Array.from(new Set(out.kinds));
  out.hit = out.kinds.length > 0;
  return out;
}
"""

# 弹层出现时，只找「关闭 / 取消」按钮返回坐标（绝不返回确认类）
FIND_CLOSE_BTN_JS = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;
  const norm = s => (s || '').replace(/\s+/g, '');
  const CLOSE_PAT = /^(关闭|取消|返回|×|✕|✖|╳|x)$/;
  let best = null;
  // ① 已知的关闭图标类名优先
  const PREFER = [/CoinExchangeModal-module_closeIcon/i, /SendGiftModal-module_close/i,
                  /Modal-module_close/i, /Dialog-module_close/i];
  const cands = [];
  document.querySelectorAll('*').forEach(e => {
    if (!vis(e)) return;
    const c = (e.className || '').toString();
    const t = norm(e.textContent);
    const r = e.getBoundingClientRect();
    if (r.width < 6 || r.height < 6) return;
    if (PREFER.some(p => p.test(c))) {
      cands.push({x: r.x + r.width/2, y: r.y + r.height/2, pri: 0, tag: 'close-icon', cls: c.slice(0,60)});
      return;
    }
    // ② 文字恰为「关闭/取消/返回」或纯图标字符，且尺寸不大（避免整页容器）
    if (CLOSE_PAT.test(t) && r.width * r.height < 220 * 120) {
      cands.push({x: r.x + r.width/2, y: r.y + r.height/2, pri: 1, tag: 'close-text', cls: c.slice(0,60)});
    }
  });
  cands.sort((a, b) => a.pri - b.pri);
  return cands[0] || null;
}
"""

# 检测页面上是否已经没有任何弹层（盲点兜底的前置条件）
NO_OVERLAY_JS = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;
  let mask = 0;
  document.querySelectorAll('[class*="Mask"], [class*="mask"], [class*="overlay"], [class*="Overlay"]').forEach(e => {
    if (!vis(e)) return;
    const cs = getComputedStyle(e);
    const bg = cs.backgroundColor || '';
    // 半透明遮罩（rgba 且 alpha < 1）才算遮挡层
    const m = bg.match(/rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(','); if (p.length >= 4 && parseFloat(p[3]) < 1) mask++; }
  });
  return {mask, anyway: mask > 0};
}
"""


class ClickAudit:
    """点击黑匣子：每次点击意图都留痕（时间 / 目标 / 是否弹层 / 是否放行）。"""

    def __init__(self, root: str, log=print):
        self.log = log
        self.dir = os.path.join(root, "logs")
        self.path = os.path.join(self.dir, "click_audit.log")
        try:
            os.makedirs(self.dir, exist_ok=True)
        except Exception:
            self.path = None

    def record(self, target: str, allowed: bool, reason: str, paywall=None,
               coords=None, action: str = "click"):
        rec = {
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "target": str(target)[:60],
            "allowed": bool(allowed),
            "reason": reason,
            "paywall": (paywall or {}).get("kinds") if isinstance(paywall, dict) else paywall,
            "coords": (round(coords[0]), round(coords[1])) if coords else None,
        }
        try:
            if self.path:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
        if not allowed:
            self.log(f"🛡 拦截点击「{rec['target']}」：{reason}")
        return rec


class Sentinel:
    """付费弹层哨兵：检测 → 记录 → 只关不确认。"""

    def __init__(self, log=print, audit: ClickAudit | None = None):
        self.log = log
        self.audit = audit
        self._last_hit = None
        self.last_paywall = None      # 最近一次扫描结果（供决策与日志）
        self._close_cooldown = 0.0

    # ---------------- 扫描 ----------------
    def scan(self, page):
        """扫描所有 frame，返回最严重的弹层信息 {hit, kinds, dom, cocos}。"""
        merged = {"hit": False, "kinds": [], "dom": [], "cocos": []}
        for f in page.frames:
            url = f.url or ""
            if url in ("", "about:blank"):
                continue
            try:
                r = f.evaluate(SCAN_PAYWALL_JS)
            except Exception:
                continue
            if not r or not r.get("hit"):
                continue
            merged["hit"] = True
            merged["kinds"] += r.get("kinds") or []
            merged["dom"] += r.get("dom") or []
            merged["cocos"] += r.get("cocos") or []
        merged["kinds"] = sorted(set(merged["kinds"]))
        self.last_paywall = merged
        return merged

    def overlay_present(self, page) -> bool:
        """页面上是否存在遮挡层（盲点兜底的前置检查）。"""
        for f in page.frames:
            url = f.url or ""
            if url in ("", "about:blank"):
                continue
            try:
                r = f.evaluate(NO_OVERLAY_JS)
            except Exception:
                continue
            if r and r.get("anyway"):
                return True
        return False

    # ---------------- 处置 ----------------
    def handle(self, page, ctx: str = "") -> bool:
        """发现弹层时的处置：记日志 + 只点关闭。返回是否发现了弹层。

        ⚠️ 本方法**永不**点击任何「确认 / 兑换 / 送出 / 立即复活 / 购买」。
        """
        r = self.scan(page)
        if not r.get("hit"):
            return False
        kinds = ",".join(r.get("kinds") or []) or "unknown"
        sig = kinds
        if sig != self._last_hit:
            self._last_hit = sig
            self.log(f"🛡 检测到付费弹层[{kinds}]"
                     + (f"（{ctx}）" if ctx else "") + " → 只关闭，不确认")
            if self.audit:
                self.audit.record("paywall-detected", False, "付费弹层出现",
                                  paywall=r, action="detect")

        # 尝试点「关闭」（每个弹层类型 2s 冷却，避免高频爆点）
        now = time.time()
        if now - self._close_cooldown < 2.0:
            return True
        self._close_cooldown = now
        self._click_close(page, r)
        return True

    def _click_close(self, page, paywall):
        """只点击关闭/取消类按钮。"""
        for f in page.frames:
            url = f.url or ""
            if url in ("", "about:blank"):
                continue
            try:
                btn = f.evaluate(FIND_CLOSE_BTN_JS)
            except Exception:
                continue
            if not btn:
                continue
            # 双重校验：关闭目标绝不允许命中危险词
            if is_danger_text(btn.get("cls", "")):
                continue
            try:
                box = None
                try:
                    box = f.frame_element().bounding_box()
                except Exception:
                    pass
                sx = btn["x"] + (box["x"] if box else 0)
                sy = btn["y"] + (box["y"] if box else 0)
                allowed, reason = is_click_allowed("关闭", extra_safe=True)
                if not allowed:
                    continue
                page.mouse.click(sx, sy)
                self.log(f"🛡 已点弹层关闭键「{btn.get('tag')}」@({sx:.0f},{sy:.0f})")
                if self.audit:
                    self.audit.record("关闭弹层", True, reason, paywall=paywall,
                                      coords=(sx, sy), action="close-overlay")
                return True
            except Exception:
                continue
        # 找不到关闭键：按 ESC 一次（不会触发任何消费）
        try:
            page.keyboard.press("Escape")
            self.log("🛡 未找到关闭键，已按 ESC（不触发消费）")
        except Exception:
            pass
        return False


def economic_gate(state: dict | None, free_left=None, coin_balance=None) -> tuple[bool, str]:
    """开局经济门闸：判断是否允许点「开始游戏」。

    ⚠️ 当前实现采取**最保守策略**：只要无法确证「免费次数 > 0」，
    就拒绝进入对局（因为西瓜币不足会自动弹兑换面板 → 可能被误点扣鱼翅）。

    Returns: (allowed, reason)
    """
    if coin_balance is not None and coin_balance <= 0 and not free_left:
        return False, "西瓜币为 0 且无免费次数 → 再点开始游戏会弹兑换面板"
    if free_left is not None and free_left <= 0 and (coin_balance is None or coin_balance <= 0):
        return False, "免费次数已用完且西瓜币不足 → 会弹兑换面板"
    return True, "经济条件未知/充足（仍由哨兵兜底）"
