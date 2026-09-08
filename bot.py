# -*- coding: utf-8 -*-
"""
斗鱼「合成大西瓜」自动化驱动层。

原理（逆向结论）：
  - 游戏是 Cocos Creator 3.8.6 构建的 H5，跑在直播间页面的 iframe 里
  - 资源根：https://shark2.douyucdn.cn/front-publish/make-watermelon-master/web-mobile/
  - 主逻辑模块：assets/main/index.c3dcd.js（gzip）里的 WatermelonGame 组件
  - 合成/半径/下一颗水果等规则由 WASM 模块 49adad64-....wasm 提供
  - 组件自带 getDebugState()：直出全部水果的 {id, level, x, y, radius, vx, vy}
  - 组件自带 dropCurrentFruitAt(x)：等价于用户点击投放

本模块负责：定位游戏 frame -> 抓取组件实例 -> 循环读状态 / 决策 / 投放。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass

import solver as solver_module
from solver import choose_drop_x, parse_debug_state, Tuning, MAX_LEVEL, replace as _tuning_replace

# 打包为 exe 后：数据目录 = exe 所在目录（登录态/配置持久化）；源码模式 = 脚本目录
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(ROOT, ".chrome-profile")   # 兼容旧值；实际用 _profile_for() 按浏览器区分
# 不再绑定单一 Chrome：运行时自动探测本机可用的 Chromium 系浏览器
# （Chrome 优先，其次 Edge/Brave 等），见 _find_browser_exe()。
# 默认直播间地址：已移除内置链接（2026-09-06），由用户在界面/启动参数中填写。
ROOM_URL = ""


def _find_browser_exe() -> str:
    """探测本机可用的 Chromium 系浏览器（不绑定单一 Chrome）。

    探测顺序：Chrome → Edge → Brave → 其它 Chromium，来源：
      1) 注册表 App Paths（覆盖非默认安装路径，最可靠）
      2) 常见安装路径兜底
    均未找到返回 ""（调用方给出清晰报错）。
    """
    import winreg

    # ① 注册表 App Paths（HKCU 优先于 HKLM，一般均存在）
    names = ("chrome.exe", "msedge.exe", "brave.exe", "chromium.exe")
    for hive_name in ("HKCU", "HKLM"):
        hive = winreg.HKEY_CURRENT_USER if hive_name == "HKCU" else winreg.HKEY_LOCAL_MACHINE
        for name in names:
            try:
                key = winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}")
                try:
                    exe, _ = winreg.QueryValueEx(key, None)
                    if exe and os.path.isfile(exe):
                        return exe
                finally:
                    winreg.CloseKey(key)
            except OSError:
                continue

    # ② 常见安装路径兜底（按 Chrome→Edge→Brave 优先级）
    env = os.environ
    cands = [
        env.get("ProgramFiles", r"C:\Program Files") + r"\Google\Chrome\Application\chrome.exe",
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)") + r"\Google\Chrome\Application\chrome.exe",
        env.get("LOCALAPPDATA", "") + r"\Google\Chrome\Application\chrome.exe",
        env.get("ProgramFiles(x86)", r"C:\Program Files (x86)") + r"\Microsoft\Edge\Application\msedge.exe",
        env.get("ProgramFiles", r"C:\Program Files") + r"\Microsoft\Edge\Application\msedge.exe",
        env.get("ProgramFiles", r"C:\Program Files") + r"\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return ""


def _profile_for(exe: str) -> str:
    """不同浏览器内核用独立 profile（避免跨内核 profile 冲突；登录态各自保存）。"""
    name = os.path.basename(exe or "").lower()
    if "edge" in name:
        return os.path.join(ROOT, ".edge-profile")
    if "brave" in name:
        return os.path.join(ROOT, ".brave-profile")
    return PROFILE

# 在 iframe 内定位 WatermelonGame 组件实例并挂到 window.__WM_GAME
FIND_GAME_JS = r"""
() => {
  if (window.__WM_GAME && window.__WM_GAME.node && window.__WM_GAME.node.isValid) {
    return {ok: true, cached: true};
  }
  const cc = window.cc || (window.cclegacy) || null;
  const getScene = () => {
    if (cc && cc.director && cc.director.getScene) return cc.director.getScene();
    return null;
  };
  const scene = getScene();
  if (!scene) return {ok: false, reason: 'no-scene'};
  const stack = [scene];
  let found = null;
  while (stack.length) {
    const n = stack.pop();
    const comps = n.components || [];
    for (const c of comps) {
      if (c && typeof c.dropCurrentFruitAt === 'function'
             && typeof c.getDebugState === 'function') {
        found = c; break;
      }
    }
    if (found) break;
    for (const ch of (n.children || [])) stack.push(ch);
  }
  if (!found) return {ok: false, reason: 'no-component'};
  window.__WM_GAME = found;
  window.__WM_CC = cc;
  return {ok: true, name: found.node && found.node.name};
}
"""

READ_STATE_JS = r"""
() => {
  const g = window.__WM_GAME;
  if (!g || !g.node || !g.node.isValid) return null;
  try { return g.getDebugState(); } catch (e) { return null; }
}
"""

# 读取「下一个水果 / 投放序列」（服务端经 battleStart 加密下发，游戏解密后存组件属性）
# 结构（2026-09-01 实时抓包确认）：
#   - serverDropFruits = [{stage, level}, ...]：服务端下发的「序列窗口」，
#     元素 = 各 stage 的水果等级。开局 fruitSeq=1 时窗口从 stage2 开始
#     （窗口[0] 即「下一颗」）。窗口随投放被消费（shift 滚动）。
#   - fruitSeq = 当前 stage 全局计数，随游戏推进持续增长，与窗口无固定索引关系；
#     服务端序列发完后游戏切本地续投（此时窗口为空、inFlight=false）。
# 历史 bug：曾用 start=fruitSeq-1 索引窗口 —— 全局计数一超过窗口长度就永久越界，
# 导致 next 永远取不到、误报「等待服务端补货」。正确做法：窗口[0] 恒为下一颗。
READ_FRUIT_QUEUE_JS = r"""
() => {
  const g = window.__WM_GAME;
  if (!g) return null;
  let cur = null;
  try {
    cur = (g.currentFruit && typeof g.currentFruit === 'object')
      ? (g.currentFruit.level ?? g.currentFruit.id ?? null) : null;
  } catch(e) {}
  const q = Array.isArray(g.serverDropFruits) ? g.serverDropFruits : [];
  const fs = (typeof g.fruitSeq === 'number') ? g.fruitSeq : null;
  let next = null, rest = [];
  if (q.length) {
    next = q[0];          // 窗口[0] = 下一颗（消费型窗口，投一颗 shift 一个）
    rest = q.slice(1);    // 剩余预取（服务端可能后续补发新段，窗口整体替换）
  }
  return {cur, fruitSeq: fs, latestStage: g.latestStageFruitListStage ?? null,
          inFlight: !!g.stageQueueRequestInFlight, next, rest};
}
"""

DROP_JS = "(x) => { const g = window.__WM_GAME; if (!g) return false; g.dropCurrentFruitAt(x); return true; }"

# 在 Cocos 场景里找按钮：匹配 Label 文字（如「开始游戏/放弃复活」）或节点名（start/giveup 等），
# 返回其世界坐标 + 可见尺寸，供换算成屏幕坐标后点击
FIND_BUTTON_JS = r"""
(text) => {
  try {
    const cc = window.cc || window.cclegacy;
    if (!cc || !cc.director) return {found:false, reason:'no-cc'};
    const scene = cc.director.getScene();
    if (!scene) return {found:false, reason:'no-scene'};
    const hints = { '开始游戏': ['start','begin','kaishi','play'],
                    '放弃复活': ['giveup','fangqi','abandon','fuhuo','revive'] };
    const lower = (text||'').toLowerCase();
    const nameHints = hints[text] || hints[lower] || [];
    let hit = null;
    const stack = [scene];
    while (stack.length && !hit) {
      const n = stack.pop();
      const comps = n.components || [];
      for (const c of comps) {
        if (c && typeof c.string === 'string'
            && c.string.replace(/\s+/g,'').indexOf(text) >= 0) { hit = n; break; }
      }
      if (!hit) {
        const nm = (n.name || '').toLowerCase();
        if (nm && nameHints.some(h => nm.indexOf(h) >= 0)) hit = n;
      }
      if (!hit) {
        const ch = n.children || [];
        for (const c of ch) stack.push(c);
      }
    }
    if (!hit) return {found:false};
    // 节点树必须全部激活才可见
    let p = hit, active = true;
    while (p) { if (p.active === false) { active = false; break; } p = p.parent; }
    if (!active) return {found:false, reason:'inactive'};
    const wp = hit.worldPosition || {x:0, y:0};
    let visW = null, visH = null;
    try { const v = cc.view.getVisibleSize(); visW = v.width; visH = v.height; } catch(e) {}
    return {found:true, x:wp.x, y:wp.y, visW, visH, name: hit.name};
  } catch(e) { return {found:false, reason:String(e)}; }
}
"""


def _simplify_layout(fruits):
    """把死亡时的全量水果压缩成分析用结构：各等级计数 + 堆顶 3 个水果。"""
    from collections import Counter
    c = Counter()
    tops = []
    for f in fruits or []:
        if not f.get("dropped") or f.get("merging"):
            continue
        lv = f.get("level") or 1
        c[lv] += 1
        tops.append((round((f.get("y") or 0) + (f.get("radius") or 0), 1),
                     lv, round(f.get("x") or 0, 1)))
    tops.sort(reverse=True)
    return {"counts": {str(k): v for k, v in sorted(c.items())},
            "top3": tops[:3]}


@dataclass
class BotStats:
    drops: int = 0
    score: int = 0
    merges: int = 0
    best_level: int = 0          # 跨局累计的历史最高等级
    best_level_game: int = 0     # 本局最高等级（每局开始重置）
    peak_watermelons: int = 0    # 本局同屏 11 级西瓜数量峰值
    drops_this_round: int = 0
    running: bool = False
    status: str = "idle"


class WatermelonBot:
    def __init__(self, log=print, tuning: Tuning | None = None):
        self.log = log
        self.tuning = tuning or Tuning()
        self.stats = BotStats()
        self.last_state = None      # 最近一次游戏调试状态（供可视化）
        self.last_plan = None       # 最近一次决策 {x, level, rest_y}
        self.last_queue = None      # 最近一次投放序列快照 {fruitSeq, next, rest, inFlight, _t}
        self._stop = threading.Event()
        self._thread = None
        self._pw = None
        self._ctx = None
        self._page = None

    # ---------- 生命周期 ----------
    def _try_enter_game(self, page):
        """点开直播间工具栏的「合成大西瓜」卡片（只点真正的卡片，不误点文字/播放器）。

        仅在面板尚未出现时调用；面板一旦出现 bot 绝不再点，避免反复 toggle。
        """
        try:
            cand = page.evaluate("""() => {
              const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;
              const norm = s => (s || '').replace(/\\s+/g, '');
              const all = [];
              document.querySelectorAll('*').forEach(e => {
                const t = norm(e.textContent);
                if (!t.includes('合成大西瓜') || !vis(e)) return;
                const cs = getComputedStyle(e);
                const r = e.getBoundingClientRect();
                const area = r.width * r.height;
                if (area <= 0 || area > 600 * 600) return;  // 跳过超大播放器
                const interactive = (e.tagName === 'BUTTON' || e.tagName === 'A'
                  || e.getAttribute('role') === 'button' || cs.cursor === 'pointer');
                if (!interactive) return;  // 只保留真正可点的入口
                all.push({x: r.x + r.width/2, y: r.y + r.height/2, area,
                          tag: e.tagName, cls: (e.className||'').toString()});
              });
              // 优先真正的卡片（class 含 tool/card/bar），取面积最大的一个
              const cards = all.filter(c => /tool|card|bar/i.test(c.cls || ''));
              const pick = (cards.length ? cards : all);
              pick.sort((a, b) => b.area - a.area);
              return pick[0] || null;
            }""")
            if not cand:
                return False
            page.mouse.click(cand["x"], cand["y"])
            self.log(f"已点开「合成大西瓜」入口 <{cand['tag']}> @({cand['x']:.0f},{cand['y']:.0f})")
            return True
        except Exception:
            return False

    def _click_dom_button(self, vibe, text: str) -> bool:
        """点击面板上的 React DOM 按钮（按文字匹配，如「开启西瓜游戏匹配PK对局」）。

        某些入口是 DOM 元素而非 canvas 绘制，坐标点击/场景查找都无效，只能 DOM 匹配。
        """
        try:
            r = vibe.evaluate("""(text) => {
              const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;
              const norm = s => (s || '').replace(/\\s+/g, '');
              let best = null;
              document.querySelectorAll('*').forEach(e => {
                const t = norm(e.textContent);
                if (!t.includes(text) || !vis(e)) return;
                const rc = e.getBoundingClientRect();
                if (rc.width < 4 || rc.height < 4) return;
                const cs = getComputedStyle(e);
                const act = (e.tagName === 'BUTTON' || e.tagName === 'A'
                  || e.getAttribute('role') === 'button' || cs.cursor === 'pointer');
                if (!act) return;
                if (!best || rc.width * rc.height > best.a) {
                  best = {x: rc.x + rc.width/2, y: rc.y + rc.height/2,
                          a: rc.width * rc.height, cls: (e.className||'').toString()};
                }
              });
              return best;
            }""", text)
            if not r:
                return False
            box = None
            try:
                box = vibe.frame_element().bounding_box()
            except Exception:
                pass
            sx = r["x"] + (box["x"] if box else 0)
            sy = r["y"] + (box["y"] if box else 0)
            self._page.mouse.click(sx, sy)
            self.log(f"已点击 DOM 按钮「{text}」@({sx:.0f},{sy:.0f})")
            return True
        except Exception:
            return False

    def _click_button(self, page, text: str, timeout: float = 10.0,
                      fallback_ratio: float | None = None,
                      fallback_fx: float = 0.5) -> bool:
        """点击游戏内 Cocos 按钮（如「开始游戏」）。

        优先在场景中找文字/按钮节点，把世界坐标换算成屏幕坐标后点击；
        找不到节点时用画布比例坐标兜底（fallback_fx, fallback_ratio 为 0~1）。
        「开始游戏」位于画布右下 (0.75, 0.92)。
        """
        vibe = None
        for f in page.frames:
            if 'vibe-lab' in (f.url or ''):
                vibe = f
                break
        if vibe is None:
            return False
        deadline = time.time() + timeout
        start_t = time.time()
        grace = min(3.0, max(1.0, timeout * 0.4))   # 找节点宽限期，之后坐标兜底
        miss_logged = False
        while time.time() < deadline and not self._stop.is_set():
            # 第 1 优先：vibe frame 内的 React DOM 按钮（面板本身是 React + canvas 混合）
            try:
                dom = vibe.evaluate("""(text) => {
                  const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;
                  const norm = s => (s || '').replace(/\\s+/g, '');
                  let best = null;
                  document.querySelectorAll('*').forEach(e => {
                    const t = norm(e.textContent);
                    if (!t.includes(text) || !vis(e)) return;
                    const r = e.getBoundingClientRect();
                    if (r.width < 4 || r.height < 4) return;
                    const act = (e.tagName === 'BUTTON' || e.tagName === 'A'
                      || e.getAttribute('role') === 'button' || getComputedStyle(e).cursor === 'pointer');
                    if (!act) return;
                    if (!best || r.width * r.height > best.a) {
                      best = {x: r.x + r.width/2, y: r.y + r.height/2,
                              a: r.width * r.height, tag: e.tagName, cls: (e.className||'').toString().slice(0,60)};
                    }
                  });
                  return best;
                }""", text)
            except Exception:
                dom = None
            if dom:
                try:
                    fbox = vibe.frame_element().bounding_box()
                    sx = (fbox["x"] if fbox else 0) + dom["x"]
                    sy = (fbox["y"] if fbox else 0) + dom["y"]
                    page.mouse.click(sx, sy)
                    self.log(f"已点击 React DOM「{text}」<{dom['tag']}> {dom['cls']} @({sx:.0f},{sy:.0f})")
                    return True
                except Exception:
                    pass
            try:
                box = vibe.frame_element().bounding_box()
                r = vibe.evaluate(FIND_BUTTON_JS, text)
            except Exception:
                box, r = None, None
            if box and r and r.get("found") and r.get("visW"):
                sx = box["x"] + box["width"] / 2 + r["x"] * (box["width"] / r["visW"])
                sy = box["y"] + box["height"] / 2 - r["y"] * (box["height"] / r["visH"])
                try:
                    page.mouse.click(sx, sy)
                    self.log(f"已点击「{text}」@({sx:.0f},{sy:.0f}) [{r.get('name')}]")
                    return True
                except Exception:
                    pass
            elif box and fallback_ratio is not None and time.time() - start_t > grace:
                sx = box["x"] + box["width"] * fallback_fx
                sy = box["y"] + box["height"] * fallback_ratio
                try:
                    page.mouse.click(sx, sy)
                    self.log(f"已点击「{text}」(坐标兜底 fx={fallback_fx:.0%} y={fallback_ratio:.0%}) @({sx:.0f},{sy:.0f})")
                    return True
                except Exception:
                    pass
            elif not miss_logged and r is not None:
                reason = (r or {}).get("reason") or "not-found"
                self.log(f"（场景中暂未找到「{text}」节点: {reason}，稍后用坐标兜底）")
                miss_logged = True
            time.sleep(1.0)
        return False

    def start(self, room_url: str = ROOM_URL, headless: bool = False):
        room_url = (room_url or "").strip()
        if not room_url:
            raise ValueError("未填写直播间地址")
        # 若旧线程还在收尾，先等它退出，再干净地重新启动
        if self._thread and self._thread.is_alive():
            self._stop.set()
            self._thread.join(timeout=5)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(room_url, headless), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self.stats.running = False
        self.stats.status = "stopped"
        # 真正关掉浏览器：下次 start 重新开一个干净的窗口，
        # 避免「停止后再开始」残留旧 Chrome/旧页面
        self._close_browser()

    def _close_browser(self):
        try:
            if self._ctx:
                self._ctx.close()
        except Exception:
            pass
        self._ctx = None
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None
        self._page = None
        # 确保浏览器进程彻底退出，避免残留占用 profile 导致下次启动秒退
        exe = getattr(self, "_browser_exe", "") or _find_browser_exe()
        self._kill_stale_browser(exe)
        self._clean_profile_locks(exe)

    def _kill_stale_browser(self, exe: str):
        """杀掉仍占用对应 profile 的残留浏览器进程（chrome/msedge/brave 均可）。"""
        try:
            import subprocess
            import tempfile
            name = os.path.basename(exe or "chrome.exe")
            prof = _profile_for(exe)
            ps1 = os.path.join(tempfile.gettempdir(), "kill_wm_browser.ps1")
            with open(ps1, "w", encoding="utf-8-sig") as f:
                f.write(
                    "$p = Get-CimInstance Win32_Process -Filter \"Name='" + name + "'\" | "
                    "Where-Object { $_.CommandLine -like '*" + prof + "*' };\n"
                    "if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }\n"
                )
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1],
                           capture_output=True, timeout=30)
        except Exception:
            pass

    def _clean_profile_locks(self, exe: str):
        prof = _profile_for(exe)
        for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie",
                     "lockfile", "DevToolsActivePort"):
            p = os.path.join(prof, lock)
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    def _ensure_browser(self, headless: bool):
        from playwright.sync_api import sync_playwright
        if self._ctx:
            return
        exe = _find_browser_exe()
        if not exe:
            raise RuntimeError(
                "未检测到 Chrome / Edge / Brave 等 Chromium 浏览器。\n"
                "请先安装 Microsoft Edge（Windows 自带，商店可装）或 Google Chrome，再重新启动。")
        self.log(f"使用浏览器: {os.path.basename(exe)}")
        self._browser_exe = exe
        self._kill_stale_browser(exe)
        self._clean_profile_locks(exe)
        last_err = None
        for attempt in range(3):
            try:
                self._pw = sync_playwright().start()
                self._ctx = self._pw.chromium.launch_persistent_context(
                    user_data_dir=_profile_for(exe),
                    executable_path=exe,
                    headless=headless,
                    viewport={"width": 1500, "height": 940},
                    args=["--disable-blink-features=AutomationControlled",
                          "--no-first-run", "--no-default-browser-check"],
                    ignore_default_args=["--enable-automation"],
                )
                self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
                return
            except Exception as e:
                last_err = e
                try:
                    if self._pw:
                        self._pw.stop()
                except Exception:
                    pass
                self._pw = None
                self._kill_stale_browser(exe)
                self._clean_profile_locks(exe)
                time.sleep(2)
        raise RuntimeError(f"浏览器启动失败(3次): {last_err}")

    def _find_game_frame(self, page, timeout: float = 600.0):
        """等待并返回真正的游戏 frame（含 WatermelonGame 组件的 Cocos 画面）。

        行为约定：
          1. 组件探测优先：各 frame 内跑 FIND_GAME_JS，命中即返回——对局开始后
             WatermelonGame 组件出现，bot 立即接管。
          2. 入口只在面板未出现时点：每 20s 尝试点开「合成大西瓜」卡片；
             vibe-lab 面板一旦出现，bot 绝不再点击，由用户手动点「开始游戏」
             开对局（面板开着时再点会把面板 toggle 关掉）。
        """
        deadline = time.time() + timeout
        last_entry_try = 0.0
        last_hint = 0.0
        # 「开始游戏」按钮位置候选列表（按截图 3：右下、底部中央、顶部中央）；
        # 每 6s 依次尝试一个位置，命中即停。
        start_positions = [(0.75, 0.92), (0.5, 0.92), (0.5, 0.5), (0.5, 0.85)]
        pos_idx = 0
        last_fbclick = 0.0
        while time.time() < deadline and not self._stop.is_set():
            frames = [f for f in page.frames
                      if (f.url or "") not in (None, "", "about:blank")]
            for f in frames:
                try:
                    r = f.evaluate(FIND_GAME_JS)
                except Exception:
                    r = None
                if r and r.get("ok"):
                    return f
            vibe_seen = any('vibe-lab' in (f.url or "") for f in frames)
            # 面板未出现 → 按需点开入口（每 20s 最多一次）
            if not vibe_seen and time.time() - last_entry_try > 20.0:
                last_entry_try = time.time()
                if self._try_enter_game(page):
                    self.log("已点开「合成大西瓜」入口，等待游戏面板加载…")
                    time.sleep(10)  # 面板渲染需要时间，别急着点开始（lobby 加载 + React 挂载）
            # 面板已开且无对局 → 自动点「开始游戏」（每 8s 轮换，React DOM 优先）
            elif vibe_seen and time.time() - last_fbclick > 8.0:
                last_fbclick = time.time()
                fx, fy = start_positions[pos_idx % len(start_positions)]
                pos_idx += 1
                self.log(f"尝试点「开始游戏」位置 ({fx:.2f},{fy:.2f})…")
                if self._click_button(page, "开始游戏", timeout=3.0,
                                      fallback_ratio=fy, fallback_fx=fx):
                    self.log(f"✓ 已点击「开始游戏」({fx:.2f},{fy:.2f})，等待对局加载…")
                    time.sleep(6)  # 点击后等对局过渡 + Cocos 加载
            self.stats.status = "waiting-game"
            time.sleep(1.0)
        return None

    # ---------- 主循环 ----------
    def _run(self, room_url: str, headless: bool):
        try:
            self._ensure_browser(headless)
            page = self._page
            self.log("打开直播间 …")
            if "douyu.com" not in (page.url or ""):
                page.goto(room_url, wait_until="domcontentloaded", timeout=60000)
            self.stats.status = "waiting-login"
            self.log("等待游戏加载（首次运行需扫码登录斗鱼）…")
            self.log("👉 首次请扫码登录；之后 bot 全自动：")
            self.log("   点开「合成大西瓜」→ 点「开始游戏」→ 投放 → 结束自动刷新 → 下一局。")

            game_no = 0
            while not self._stop.is_set():
                frame = self._find_game_frame(page, timeout=600)
                if frame is None:
                    self.log("未找到游戏 frame。请确认已登录且游戏面板可打开。")
                    self.stats.status = "no-game"
                    return

                self.log(f"游戏 frame: {frame.url[:120]}")
                self.stats.status = "binding"

                # 每局开始前清掉上一局的序列缓存，避免新局误用旧队列
                self.last_queue = None
                self._last_qsig = None
                self.last_plan = None

                # 绑定组件实例
                game = None
                for _ in range(60):
                    if self._stop.is_set():
                        return
                    try:
                        r = frame.evaluate(FIND_GAME_JS)
                    except Exception as e:
                        r = {"ok": False, "reason": str(e)[:80]}
                    if r and r.get("ok"):
                        game = True
                        self.log(f"已绑定游戏组件：{r.get('name')}")
                        break
                    time.sleep(1.0)
                if not game:
                    self.log("无法绑定游戏组件实例，重试…")
                    time.sleep(2.0)
                    continue

                game_no += 1
                self._game_no = game_no
                self._drops_at_start = self.stats.drops
                self.stats.best_level_game = 0      # 每局重置，便于统计单局成绩
                self.stats.peak_watermelons = 0
                self.log(f"—— 第 {game_no} 局开始 ——")
                self.stats.running = True
                self.stats.status = "playing"
                self._loop(frame, page)          # 局内循环：结束/异常时返回
                if self._stop.is_set():
                    return
                time.sleep(5.0)                   # 回主页过渡，外层会自动点「开始游戏」

        except Exception as e:
            if self._stop.is_set():
                return  # 停止过程中关闭浏览器导致的异常，静默退出
            self.log(f"[错误] {type(e).__name__}: {e}")
            self.stats.status = f"error: {e}"
        finally:
            self.stats.running = False

    def _loop(self, frame, page):
        last_score = 0
        idle_since = None
        last_drop_at = 0.0
        prev_fruit_count = 0
        # 分析采集（对局级，每局重置）
        self._milestones = []          # 每突破一个等级记录 [{lv, atDrop(本局第几投), score}]
        self._milestone_max = 0
        self._layout_snapshot = []     # 死亡前最后一次 isPlaying 的水果快照（gameOver 后场地会清空）
        self._seq_trace = []           # 服务端序列窗口追踪（诊断）
        # 重启保护：state.gameOver=true 在上一局结算后可能短暂残留，
        # 必须先观察到 isPlaying=true（即新一局真正开始过），才能承认新一轮的 gameOver。
        # 否则 bot 会陷入"放弃复活 → 立刻判 gameOver → 又放弃复活 → …"的 4 秒一局死循环。
        played_this_round = False
        start_at = time.time()

        while not self._stop.is_set():
            try:
                state = frame.evaluate(READ_STATE_JS)
            except Exception as e:
                self.stats.status = f"read-error: {str(e)[:60]}"
                time.sleep(0.5)
                continue

            if not state:
                time.sleep(0.3)
                continue

            self.last_state = state
            self.stats.score = state.get("score", 0) or 0
            fruits = state.get("fruits") or []
            lvl = max([f.get("level", 0) for f in fruits] or [0])
            self.stats.best_level = max(self.stats.best_level, lvl)              # 历史累计
            self.stats.best_level_game = max(self.stats.best_level_game, lvl)     # 本局
            n11 = sum(1 for f in fruits if (f.get("level") or 0) >= MAX_LEVEL)    # 同屏西瓜数
            if n11 > self.stats.peak_watermelons:
                self.stats.peak_watermelons = n11
            # 分析采集：每突破一个新等级，记录当时的本局投放进度/分数（合成路径断点）
            if lvl > self._milestone_max:
                self._milestone_max = lvl
                self._milestones.append({"lv": lvl,
                                         "atDrop": self.stats.drops - getattr(self, "_drops_at_start", 0),
                                         "score": self.stats.score})

            # 持续刷新投放序列（服务端下发的序列窗口，一有变化立即记录）
            try:
                tq = time.time()
                if tq - getattr(self, "_last_qpoll", 0.0) > 1.0:
                    self._last_qpoll = tq
                    q = frame.evaluate(READ_FRUIT_QUEUE_JS)
                    if q:
                        q["_t"] = time.time()   # 供投放处 2s 内复用，避免每次投放前重复 evaluate
                        self.last_queue = q
                        nxt = q.get("next") or {}
                        nlv = nxt.get("level")
                        rest_lv = [r.get("level") for r in (q.get("rest") or [])]
                        sig = (nlv, tuple(rest_lv), q.get("fruitSeq"))
                        if sig != getattr(self, "_last_qsig", None):
                            self._last_qsig = sig
                            # 序列追踪（诊断用）：每颗窗口变化记一次
                            try:
                                if nlv is not None:
                                    self._seq_trace.append({
                                        "seq": q.get("fruitSeq"),
                                        "next": nlv,
                                        "rest": list(rest_lv),
                                        "drop_no": self.stats.drops,
                                        "max_lv": max([f.get("level", 0) for f in fruits] or [0]),
                                    })
                            except Exception:
                                pass
                            if nlv is None and not rest_lv:
                                # 窗口耗尽：区分「正在补货」与「服务端序列已发完（本地续投）」
                                if q.get("inFlight"):
                                    self.log("📥 序列窗口耗尽，服务端补货中…")
                                elif not getattr(self, "_seq_drained_logged", False):
                                    self._seq_drained_logged = True
                                    self.log("📥 服务端水果序列已耗尽，游戏本地续投（无前瞻）")
                            else:
                                self._seq_drained_logged = False
                                inf = "（补货中…）" if q.get("inFlight") else ""
                                self.log(f"📥 序列更新: 下一个lv{nlv}"
                                         f" 预取[{','.join('lv'+str(l) for l in rest_lv)}]{inf}")
            except Exception:
                pass

            if state.get("isPlaying"):
                # 标记本局确实"开始过"——之后的 gameOver 才能被承认
                played_this_round = True
                # 死亡前快照：场地有水果时持续刷新（gameOver 后水果会被清空）
                if fruits:
                    self._layout_snapshot = fruits
            if state.get("gameOver") and played_this_round:
                self.stats.status = "game-over"
                self.stats.drops_this_round = self.stats.drops - getattr(self, "_drops_at_start", 0)
                self._drops_at_start = self.stats.drops
                # 记录本局战绩（供统计「每轮能合成几个大西瓜」）
                hist = list(getattr(self, "game_history", []) or [])
                hist.append({
                    "game": getattr(self, "_game_no", len(hist) + 1),
                    "score": self.stats.score,
                    "bestLevel": self.stats.best_level_game,
                    "watermelons": self.stats.peak_watermelons,
                    "drops": self.stats.drops_this_round,
                    "milestones": list(getattr(self, "_milestones", [])),     # 合成路径断点
                    "finalLayout": _simplify_layout(getattr(self, "_layout_snapshot", None)
                                                    or state.get("fruits") or []),  # 死亡布局
                })
                self.game_history = hist[-20:]   # 只留最近 20 局
                self.log(f"📊 本局战绩: 最高 lv{self.stats.best_level_game}  "
                         f"大西瓜×{self.stats.peak_watermelons}  "
                         f"得分 {self.stats.score}  投放 {self.stats.drops_this_round}")

                self.log(f"游戏结束，得分 {self.stats.score}，最高等级 {self.stats.best_level_game}，刷新页面重开…")
                # 方案（2026-09-01 22:30 用户确认）：不点「放弃复活」按钮，
                # 直接刷新页面回到直播间 → 外层循环自动重新点开「合成大西瓜」入口并点「开始游戏」
                try:
                    page.reload(wait_until="domcontentloaded", timeout=60000)
                    self.log("已刷新页面，等待重新打开游戏…")
                except Exception as e:
                    self.log(f"[刷新失败] {e}")
                time.sleep(8.0)   # 等直播间 DOM/组件恢复，避免立即点开始误点到残留
                return

            if not state.get("isPlaying"):
                # 组件在但未在对局：可能是结算界面/过渡。
                # 第 2 局起可能遇到"点击开始后加载慢"，等 35s 仍没开始才回外层重试点「开始游戏」
                # （不点「放弃复活」——主页上该位置可能点到别的东西，误操作会把面板关掉）
                if not played_this_round and time.time() - start_at > 35.0:
                    self.log("新局 35s 未真正开始，回到外层重新点「开始游戏」…")
                    time.sleep(2.0)
                    return
                self.stats.status = "idle-in-menu"
                time.sleep(0.6)
                continue

            self._menu_since = None
            field, cur_level, settled = parse_debug_state(state)
            if cur_level is None:
                time.sleep(0.15)
                continue

            now = time.time()
            # —— 静止判定（2026-09-02 22:35 回滚）——
            # 曾收紧为「速度<3+4帧稳定」：实测 3 局平均 lv9.3 / 0 西瓜，反不如旧版
            # （lv10.3 / 1 西瓜）。原因：等全静止会固化布局，大果被小果隔开后无法滚动
            # 汇聚；快节奏（旧版 speed<12，~1s/颗）的碰撞推挤反而帮助大果凑对。
            # 恢复旧判定：parse_debug_state 的 settled（moving speed>12）+ 稳定 0.25s。
            if not settled:
                idle_since = None
                self.stats.status = "settling"
                time.sleep(0.12)
                continue

            if idle_since is None:
                idle_since = now
            if now - idle_since < 0.25:      # 静止需稳定一小段
                time.sleep(0.08)
                continue
            if now - last_drop_at < 0.55:    # 游戏内置 0.45s 冷却，留余量
                time.sleep(0.08)
                continue

            # —— 方案③ wm-D：数据节奏自适应 ——
            # 用本局 milestones 对照历史健康线（v1 快节奏平均达成投数）判断是否「合成停滞」。
            # 停滞 = 最高等级长时间没突破（如 100 投还没 lv8）→ 场上多半是小果积压、互相隔离，
            # 继续按 v1 追大果只会堆高；切「清小果模式」：强化小果合成与平整，弱化大果聚拢。
            tune_eff = self.tuning
            if getattr(self.tuning, "dmode", 0.0) > 0:
                ms = list(getattr(self, "_milestones", []) or [])
                top_lv = max([m["lv"] for m in ms] or [0])
                drops = self.stats.drops - getattr(self, "_drops_at_start", 0)
                # 历史健康线：v1 达成各等级时的典型投数（越高级越宽松，取自历次实测中位数）
                HEALTH = {3: 6, 4: 10, 5: 16, 6: 26, 7: 40, 8: 60, 9: 90, 10: 140}
                lag = HEALTH.get(top_lv, 999)
                stalled = drops > lag * 1.6 if lag < 999 else False
                # 小果占比过高也是停滞信号（场上积压消化不掉）
                small_share = 0.0
                fs_all = [f for f in fruits if f.get("dropped")]
                if len(fs_all) >= 6:
                    small_share = sum(1 for f in fs_all if (f.get("level") or 9) <= 3) / len(fs_all)
                if stalled and top_lv >= 5 or (small_share > 0.45 and drops > 50):
                    if not getattr(self, "_dmode_warned", False):
                        self.log(f"⚠ 数据节奏检测: {drops}投仍卡 lv{top_lv}（健康线{lag}）"
                                 f" 小果占比{small_share:.0%} → 切清小果模式")
                        self._dmode_warned = True
                    tune_eff = _tuning_replace(self.tuning, same_level_affinity=600.0,
                                               isolated_penalty=300.0, height_penalty=5.0,
                                               level_layering=600.0)
                else:
                    self._dmode_warned = False

            # 先读取「下一个水果 / 后续序列」（服务端 battleStart 加密下发，组件内为明文）
            # 优先用持续轮询的最新结果（1s 内），否则现场读一次
            nxt_txt = ""
            future = []
            try:
                q = getattr(self, "last_queue", None)
                if not q or time.time() - (q.get("_t") or 0) > 2.0:
                    q = frame.evaluate(READ_FRUIT_QUEUE_JS)
                if q:
                    nlv = ((q.get("next") or {}).get("level")) if q.get("next") else None
                    nst = ((q.get("next") or {}).get("stage")) if q.get("next") else None
                    rest_lv = [r.get("level") for r in (q.get("rest") or [])]
                    future = ([nlv] if nlv else []) + rest_lv[:5]   # 前瞻用未来几颗
                    self.last_plan.update({
                        "nextFruit": nlv, "nextStage": nst,
                        "fruitQueue": rest_lv[:8], "fruitSeq": q.get("fruitSeq"),
                        "inFlight": q.get("inFlight"),
                    })
                    inf = "（补货中…）" if q.get("inFlight") else ""
                    if nlv is None and not rest_lv:
                        nxt_txt = ("（服务端序列已耗尽，本地续投）" if not q.get("inFlight")
                                   else "（序列补货中…）")
                    else:
                        nxt_txt = (f" 下一个:lv{nlv}(第{nst}个)"
                                   f" 后续[{','.join('lv'+str(l) for l in rest_lv[:8])}]{inf}")
            except Exception:
                pass

            # 决策：未来序列纳入前瞻。默认 2 颗贪心；wm-H(beam) 需更深窗口发挥分头演化优势
            beam_on = getattr(tune_eff, "beam_width", 1) > 1
            fd = 5 if beam_on else 2
            x = choose_drop_x(field, cur_level, tune_eff,
                              future_levels=future, future_depth=fd)
            _, rest_y = (None, None)
            try:
                _, rest_y = solver_module.simulate_drop(
                    field, x, solver_module.radius_of(cur_level), self.tuning)
            except Exception:
                pass
            self.last_plan = {"x": x, "level": cur_level, "rest_y": rest_y}

            try:
                frame.evaluate(DROP_JS, float(x))
            except Exception as e:
                self.log(f"[投放失败] {e}")
                time.sleep(0.5)
                continue

            self.stats.drops += 1
            last_drop_at = now
            idle_since = None
            self.stats.status = "playing"
            self.log(f"#{self.stats.drops:3d} 投放 lv{cur_level} @ x={x:7.1f}  分数={self.stats.score}{nxt_txt}")

            if self.stats.score != last_score:
                last_score = self.stats.score

    # ---------- 清理 ----------
    def close(self):
        self.stop()
        self._close_browser()
