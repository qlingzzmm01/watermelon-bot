# -*- coding: utf-8 -*-
"""
合成大西瓜自动化 —— 本地 Web 控制台。

启动后浏览器访问 http://127.0.0.1:8733
后端用标准库 http.server，无额外依赖。
"""
from __future__ import annotations

import json
import mimetypes
import os
import sys
import threading
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from solver import Tuning, PRESETS
import bot as bot_mod

# 打包为 exe 后：
#   ROOT = exe 所在目录（.chrome-profile 等数据）；WEB = 解包资源目录(_MEIPASS)/web
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
    _MEI = getattr(sys, "_MEIPASS", ROOT)
    WEB = os.path.join(_MEI, "web")
else:
    ROOT = os.path.dirname(os.path.abspath(__file__))
    WEB = os.path.join(ROOT, "web")
PORT = 8733

LOGS = deque(maxlen=300)


def _log(msg):
    LOGS.append({"t": __import__("time").strftime("%H:%M:%S"), "msg": str(msg)})


BOT = bot_mod.WatermelonBot(log=_log)
# 默认「default」= v1+鲁棒落点（实证最优）；「default2」= 纯 v1
BOT.tuning = PRESETS["default"]
_CURRENT_PRESET = "default"

TUNING_FIELDS = ("merge_reward", "chain_bonus", "height_penalty", "danger_penalty",
                 "surface_bonus", "big_on_small_penalty", "flat_bonus", "edge_penalty",
                 "samples", "sim_step", "sim_max_iter",
                 "level_curve", "level_gain_scale", "same_level_affinity",
                 "level_layering", "isolated_penalty", "milestone_reward",
                 "small_high_penalty", "side_penalty", "crowd_penalty", "danger_ramp",
                 "pair_boost", "pair_decay", "roll_factor",
                 "pair_slot_reward", "pair_slot_scale", "danger_curve", "danger_curve_start",
                 "robust_amp", "beam_width", "beam_depth", "dmode")


def pack_tuning(t: Tuning) -> dict:
    return {k: getattr(t, k) for k in TUNING_FIELDS}


def tuning_from_dict(d: dict) -> Tuning:
    from dataclasses import fields
    t = Tuning()
    fmap = {f.name: f for f in fields(Tuning)}
    for k in TUNING_FIELDS:
        if k not in d or k not in fmap:
            continue
        try:
            if fmap[k].type is int:
                setattr(t, k, int(d[k]))
            else:
                setattr(t, k, float(d[k]))
        except (TypeError, ValueError):
            pass
    return t


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str):
        full = os.path.join(WEB, path.lstrip("/") or "index.html")
        if not os.path.abspath(full).startswith(os.path.abspath(WEB)):
            return self._json({"error": "forbidden"}, 403)
        if not os.path.isfile(full):
            full = os.path.join(WEB, "index.html")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        data = open(full, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/"):
            return self.api(u.path, None)
        return self._file(u.path)

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        return self.api(u.path, payload)

    def api(self, path: str, payload):
        if path == "/api/status":
            st = BOT.last_state or {}
            q = getattr(BOT, "last_queue", None)
            return self._json({
                "running": BOT.stats.running,
                "status": BOT.stats.status,
                "drops": BOT.stats.drops,
                "score": BOT.stats.score,
                "bestLevel": BOT.stats.best_level,
                "bestLevelGame": BOT.stats.best_level_game,
                "peakWatermelons": BOT.stats.peak_watermelons,
                "gameNo": getattr(BOT, "_game_no", 0),
                "gameHistory": list(getattr(BOT, "game_history", []) or []),
                "plan": BOT.last_plan,
                "queue": {
                    "fruitSeq": q.get("fruitSeq") if q else None,
                    "next": (q.get("next") or {}).get("level") if q and q.get("next") else None,
                    "nextStage": (q.get("next") or {}).get("stage") if q and q.get("next") else None,
                    "rest": [r.get("level") for r in (q.get("rest") or [])] if q else [],
                    "inFlight": q.get("inFlight") if q else False,
                },
                "state": {
                    "score": st.get("score"),
                    "isPlaying": st.get("isPlaying"),
                    "gameOver": st.get("gameOver"),
                    "layout": st.get("layout"),
                    "fruits": st.get("fruits"),
                    "currentFruitId": st.get("currentFruitId"),
                    "updatedAt": st.get("updatedAt"),
                },
                "logs": list(LOGS)[-80:],
            })

        if path == "/api/start":
            if BOT.stats.running:
                return self._json({"ok": False, "msg": "已在运行"})
            if payload.get("tuning"):
                BOT.tuning = tuning_from_dict(payload["tuning"])
            room = (payload.get("room") or "").strip() or bot_mod.ROOM_URL
            headless = bool(payload.get("headless"))
            BOT.start(room_url=room, headless=headless)
            return self._json({"ok": True})

        if path == "/api/stop":
            BOT.stop()
            return self._json({"ok": True})

        if path == "/api/tuning":
            # 2026-09-04 修复：GET（payload=None）只读，不能把 bot 配置重置成默认值
            if payload:
                BOT.tuning = tuning_from_dict(payload)
                globals()["_CURRENT_PRESET"] = "custom"
            return self._json({"ok": True, "tuning": pack_tuning(BOT.tuning)})

        if path == "/api/shutdown":
            BOT.close()
            threading.Thread(target=lambda: (self.server.shutdown()), daemon=True).start()
            return self._json({"ok": True})

        if path == "/api/presets":
            # 返回全部预设及其参数，供前端渲染按钮
            out = {name: pack_tuning(t) for name, t in PRESETS.items()}
            return self._json({"ok": True, "presets": out, "current": _CURRENT_PRESET})

        if path == "/api/preset":
            name = (payload or {}).get("name")
            if name not in PRESETS:
                return self._json({"ok": False, "msg": f"未知预设: {name}"})
            BOT.tuning = PRESETS[name]
            globals()["_CURRENT_PRESET"] = name
            return self._json({"ok": True, "name": name, "tuning": pack_tuning(BOT.tuning)})

        return self._json({"error": "not found"}, 404)


def main():
    os.makedirs(WEB, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"控制台已启动: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        BOT.close()


if __name__ == "__main__":
    main()
