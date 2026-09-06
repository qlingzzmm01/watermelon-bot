# -*- coding: utf-8 -*-
"""
合成大西瓜自动化 —— 桌面版（试用）。

一体化：tkinter 界面 + bot 逻辑直连，无需浏览器打开本地网页控制台。
功能与网页版等价：
  - 开始/停止（自动点入口 → 开始游戏 → 投放 → gameOver 重开）
  - 策略切换：default（默认 · 鲁棒落点）/ default2（默认2 · 纯 v1）
  - 实时场地画布 + 合成路径日志 + 战绩统计

依赖：python3.14（自带 tkinter）+ playwright（系统 Python 已装 1.62）
运行：python desktop_app.py    打包：见 合成大西瓜桌面版.spec
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

from solver import Tuning, PRESETS
import bot as bot_mod


# windowed exe 无控制台：全局异常写文件便于诊断
def _excepthook(et, ev, tb):
    try:
        import traceback
        logp = os.path.join(_ROOT, "crash.log")
        with open(logp, "a", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(et, ev, tb)))
    except Exception:
        pass
    sys.__excepthook__(et, ev, tb)


sys.excepthook = _excepthook

# 匿名数据上报（opt-in 默认开，仅在配置 ENDPOINT 后真正联网；不含账号/直播间信息）
import telemetry as telemetry_mod

# ---------- 常量 ----------
FRUITS = {
    1: ('蓝莓', '#3E62B1'), 2: ('沃柑', '#FAA618'), 3: ('青橘', '#A0CD58'),
    4: ('火龙果', '#E4183F'), 5: ('猕猴桃', '#ADC547'), 6: ('苹果', '#C5202B'),
    7: ('桃子', '#F98D73'), 8: ('菠萝', '#F8C430'), 9: ('椰子', '#7B442B'),
    10: ('半边西瓜', '#EA5C3B'), 11: ('大西瓜', '#518935'),
}
STATUS_TEXT = {
    'playing': '运行中', 'settling': '等待稳定', 'idle-in-menu': '等待开局',
    'game-over': '本局结束', 'waiting-game': '等待游戏加载', 'waiting-login': '等待登录',
    'binding': '绑定游戏组件', 'no-game': '未找到游戏', 'bind-failed': '绑定失败',
    'idle': '未启动', 'stopped': '已停止',
}

# 统一访问 bot / solver 的根目录（源码运行=本文件目录；exe=解包目录）
if getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(sys.executable)
else:
    _ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)


class DesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.log_q = queue.Queue()          # 后台线程 -> UI 日志
        self._logs_done = 0
        self._cur_preset = "default"
        self._reported_games = 0            # 已上报局数游标（game_history 增量检测）

        root.title("🍉 合成大西瓜 · 桌面自动化")
        root.geometry("1180x800")
        root.minsize(1000, 680)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.bot = bot_mod.WatermelonBot(log=self._bot_log)
        self.bot.tuning = PRESETS["default"]

        # 匿名上报：opt-in 默认开（未配置 ENDPOINT 则不联网）
        self.telemetry = telemetry_mod.get_telemetry(
            _ROOT, enabled=True, log=self._bot_log)
        self.telemetry.start()

        self._build_ui()
        self._tick()                          # 启动周期刷新
        root.after(150, self._poll_logs)

    # ---------------- UI ----------------
    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}
        main = ttk.Frame(self.root); main.pack(fill="both", expand=True, padx=12, pady=10)

        # 左：控制与统计
        left = ttk.Frame(main, width=340); left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)

        ttk.Label(left, text="控制", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", **pad)
        ctl = ttk.LabelFrame(left, text=" 开始 / 停止 ")
        ctl.pack(fill="x", padx=4, pady=4)

        ttk.Label(ctl, text="直播间地址（必填，如 https://www.douyu.com/123456）").pack(anchor="w", padx=8, pady=(6, 0))
        self.room_var = tk.StringVar(value="")
        ttk.Entry(ctl, textvariable=self.room_var).pack(fill="x", padx=8, pady=4)

        self.headless_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctl, text="无头模式（不显示浏览器窗口）",
                        variable=self.headless_var).pack(anchor="w", padx=8)

        btns = ttk.Frame(ctl); btns.pack(fill="x", padx=8, pady=8)
        self.btn_start = ttk.Button(btns, text="▶ 开始自动合成", command=self._on_start)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_stop = ttk.Button(btns, text="■ 停止", command=self._on_stop, state="disabled")
        self.btn_stop.pack(side="left", fill="x", expand=True)

        # 策略切换
        st = ttk.LabelFrame(left, text=" 策略 ")
        st.pack(fill="x", padx=4, pady=6)
        self.preset_var = tk.StringVar(value="default")
        for key, label in (("default", "默认 ★ 鲁棒落点"), ("default2", "默认2 · 纯 v1")):
            ttk.Radiobutton(st, text=label, value=key, variable=self.preset_var,
                            command=self._on_preset).pack(anchor="w", padx=8, pady=2)

        # 运行状态
        run = ttk.LabelFrame(left, text=" 运行状态 ")
        run.pack(fill="x", padx=4, pady=6)
        self.badge_var = tk.StringVar(value="未启动")
        ttk.Label(run, textvariable=self.badge_var, font=("Microsoft YaHei UI", 11, "bold"),
                  foreground="#0E9F6E").pack(anchor="w", padx=8, pady=4)

        grid = ttk.Frame(run); grid.pack(fill="x", padx=6, pady=2)
        self.stats = {}
        items = [("投放次数", "drops"), ("当前分数", "score"),
                 ("本局最高lv", "bestLevelGame"), ("场上水果", "fruits"),
                 ("西瓜×本局", "watermelons"), ("完成局数", "games")]
        for i, (label, key) in enumerate(items):
            f = ttk.Frame(grid); f.grid(row=i // 2, column=i % 2, sticky="ew", padx=4, pady=3)
            grid.columnconfigure(i % 2, weight=1)
            ttk.Label(f, text=label, font=("Microsoft YaHei UI", 9),
                      foreground="#555").pack(anchor="w")
            v = tk.StringVar(value="-")
            self.stats[key] = v
            ttk.Label(f, textvariable=v, font=("Consolas", 14, "bold")).pack(anchor="w")

        self.plan_var = tk.StringVar(value="等待决策…")
        ttk.Label(run, textvariable=self.plan_var, wraplength=300,
                  justify="left", font=("Microsoft YaHei UI", 9),
                  foreground="#444").pack(anchor="w", padx=8, pady=(6, 8))

        # 数据上报开关（opt-in）
        self.telemetry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(run, text="匿名上报对局数据（用于策略优化，不含账号/直播间）",
                        variable=self.telemetry_var,
                        command=self._on_telemetry_toggle).pack(anchor="w", padx=8, pady=(0, 2))
        self.telemetry_state = tk.StringVar(
            value="已开启 ✓" if self.telemetry.enabled else "未配置端点（本地不联网）")
        ttk.Label(run, textvariable=self.telemetry_state, font=("Microsoft YaHei UI", 8),
                  foreground="#8A9099").pack(anchor="w", padx=8, pady=(0, 8))

        # 右：场地画布 + 日志
        right = ttk.Frame(main); right.pack(side="right", fill="both", expand=True)

        stage_frame = ttk.LabelFrame(right, text=" 实时场地 ")
        stage_frame.pack(fill="both", expand=True, pady=(0, 6))
        self.cv = tk.Canvas(stage_frame, width=520, height=470,
                            bg="#FDF3DE", highlightthickness=1,
                            highlightbackground="#D8C9A8")
        self.cv.pack(fill="both", expand=True, padx=6, pady=6)
        self.cv.bind("<Configure>", lambda e: None)

        log_frame = ttk.LabelFrame(right, text=" 运行日志 ")
        log_frame.pack(fill="x", pady=(0, 0))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=9, state="disabled", font=("Consolas", 9),
            bg="#0F172A", fg="#CBD5E1", wrap="word")
        self.log_text.pack(fill="x", padx=6, pady=6)

    # ---------------- bot 桥接 ----------------
    def _bot_log(self, msg):
        """bot 后台线程回调：压入队列，由 UI 轮询取出（tkinter 非线程安全）。"""
        try:
            self.log_q.put((time.strftime("%H:%M:%S"), str(msg)))
        except Exception:
            pass

    def _on_start(self):
        if self.bot.stats.running:
            return
        room = self.room_var.get().strip()
        if not room:
            self._bot_log("⚠ 请先填写直播间地址（如 https://www.douyu.com/123456）")
            messagebox.showwarning("缺少直播间地址", "请先填写直播间地址，\n如：https://www.douyu.com/123456")
            return
        headless = bool(self.headless_var.get())
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        threading.Thread(target=self.bot.start,
                         args=(room, headless), daemon=True).start()
        self._bot_log("▶ 启动中：打开浏览器 → 进入直播间 → 开始自动合成")

    def _on_stop(self):
        threading.Thread(target=self.bot.stop, daemon=True).start()
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")

    def _on_preset(self):
        name = self.preset_var.get()
        if name in PRESETS:
            self.bot.tuning = PRESETS[name]
            self._cur_preset = name
            self._bot_log(f"策略切换: {name}")

    def _on_telemetry_toggle(self):
        on = bool(self.telemetry_var.get())
        if on:
            self.telemetry.start()
            self.telemetry_state.set("已开启 ✓" if self.telemetry.enabled
                                     else "已开启（未配置端点，本地不联网）")
        else:
            self.telemetry.stop()
            self.telemetry_state.set("已关闭")

    def _report_new_games(self):
        """增量上报已完成的局（每局 game_over 后 game_history 追加一条）。"""
        try:
            hist = getattr(self.bot, "game_history", None) or []
            total = len(hist)
            while self._reported_games < total:
                g = hist[self._reported_games]
                self._reported_games += 1
                if not isinstance(g, dict):
                    continue
                rec = dict(g)
                rec.setdefault("preset", self._cur_preset)
                self.telemetry.report(rec)
        except Exception:
            pass

    def _on_close(self):
        try:
            self.telemetry.stop()
        except Exception:
            pass
        try:
            self.bot.close()
        except Exception:
            pass
        self.root.destroy()

    # ---------------- 周期刷新 ----------------
    def _poll_logs(self):
        """把后台线程日志灌入 UI。"""
        while True:
            try:
                t, msg = self.log_q.get_nowait()
            except queue.Empty:
                break
            self.log_text.config(state="normal")
            self.log_text.insert("end", f"[{t}] {msg}\n")
            self.log_text.see("end")
            self.log_text.config(state="disabled")
        self.root.after(200, self._poll_logs)

    def _tick(self):
        """每 300ms 刷新状态卡片 / 画布 / 决策信息 / 对局上报。"""
        try:
            self._report_new_games()
            st = self.bot.stats
            self.badge_var.set(STATUS_TEXT.get(st.status, st.status))
            self.stats["drops"].set(str(st.drops))
            self.stats["score"].set(str(st.score))
            self.stats["bestLevelGame"].set(str(st.best_level_game))
            self.stats["watermelons"].set(str(st.peak_watermelons))
            self.stats["games"].set(str(len(getattr(self.bot, "game_history", []) or [])))
            n_fruits = len([f for f in (self.bot.last_state or {}).get("fruits", [])
                            if f.get("dropped")]) if self.bot.last_state else 0
            self.stats["fruits"].set(str(n_fruits or "-"))

            p = self.bot.last_plan or {}
            if p.get("x") is not None:
                txt = f"决策：lv{p.get('level')} → x={p.get('x'):.1f}"
                nxt = p.get("nextFruit")
                if nxt is not None:
                    txt += f"　🍉 下一个:lv{nxt}"
                q = getattr(self.bot, "last_queue", None)
                if q and (q.get("rest") or []):
                    txt += f"　后续:[{','.join('lv'+str(x) for x in q['rest'][:6])}]"
                self.plan_var.set(txt)
            self._draw_stage()
        except Exception:
            pass
        self.root.after(300, self._tick)

    # ---------------- 场地绘制 ----------------
    def _draw_stage(self):
        cv = self.cv
        cv.delete("all")
        st = self.bot.last_state or {}
        lay = st.get("layout") or {}
        if not lay:
            cv.create_text(260, 235, text="等待游戏数据…", fill="#8A9099",
                           font=("Microsoft YaHei UI", 13))
            return
        W, H = cv.winfo_width(), cv.winfo_height()
        top, bot = lay.get("playTop", 480), lay.get("playBottom", -504)
        pw = lay.get("playWidth", 684)
        danger = lay.get("dangerY", 452)
        s = min(H / (top - bot), W / pw)
        ox, oy = W / 2, H - 4

        def X(gx):
            return ox + gx * s

        def Y(gy):
            return oy - (gy - bot) * s

        # 场地边框
        cv.create_rectangle(X(-pw / 2), Y(top), X(pw / 2), Y(bot),
                            outline="#E3D3B4", width=2)
        # 死亡线
        if danger:
            cv.create_line(X(-pw / 2), Y(danger), X(pw / 2), Y(danger),
                           fill="#E02424", width=1, dash=(7, 5))
            cv.create_text(X(-pw / 2) + 6, Y(danger) - 6, text="死亡线",
                           fill="#E02424", font=("Microsoft YaHei UI", 8))
        # 决策落点（绿色虚线）
        p = self.bot.last_plan or {}
        if p.get("x") is not None and st.get("isPlaying"):
            xp = X(p["x"])
            cv.create_line(xp, Y(top), xp, Y(bot), fill="#0E9F6E", width=1, dash=(4, 4))
        # 水果
        for f in st.get("fruits") or []:
            lv = f.get("level", 1)
            info = FRUITS.get(lv, ("?", "#999999"))
            cx, cy = X(f.get("x", 0)), Y(f.get("y", 0))
            r = max(3.0, (f.get("radius") or 20) * s)
            fill = info[1]
            cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                           fill=fill, outline="")
            if not f.get("dropped"):
                cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                               fill="", outline="rgba(0,0,0,.25)")
            if r > 10:
                cv.create_text(cx, cy, text=str(lv), fill="#ffffff",
                               font=("Consolas", max(8, int(r * 0.6)), "bold"))
        # 图例
        ly = 8
        for lv in (1, 4, 7, 9, 11):
            col = FRUITS[lv][1]
            cv.create_oval(X(-pw / 2) + 2, Y(top) + ly + 7, X(-pw / 2) + 2 + 14, Y(top) + ly + 21,
                           fill=col, outline="")
            cv.create_text(X(-pw / 2) + 24, Y(top) + ly + 14, text=f"lv{lv} {FRUITS[lv][0]}",
                           fill="#555", anchor="w", font=("Microsoft YaHei UI", 8))
            ly += 20


def main():
    root = tk.Tk()
    app = DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
