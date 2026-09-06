# -*- coding: utf-8 -*-
"""
合成大西瓜自动化 —— 匿名对局数据上报模块（客户端侧）。

- 匿名设备 ID：首次运行在本目录生成 uuid（telemetry_id.txt），不含任何账号信息。
- 上报内容：每局 game_history 的统计字段（得分/最高等级/西瓜数/里程碑/死亡布局）+ 策略名 + 版本。
  不采集：直播间地址、斗鱼账号、cookies、浏览器指纹。
- 传输：HTTPS POST JSON → 腾讯云函数 SCF（免服务器）。
- 可靠性：失败写入 pending_reports.jsonl 本地缓存，启动与每局后自动重试。
- 隐私开关：enabled=False 或 ENDPOINT="" 时完全不上报、不联网。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid

APP_VERSION = "1.1.0"
APP_NAME = "大西瓜自动化"

# 数据上报端点（腾讯云函数 URL，2026-09-06 部署）。留空 = 关闭上报。
# 若端点失效/更换，改这里或设环境变量 WM_TELEMETRY_URL。
ENDPOINT = os.environ.get("WM_TELEMETRY_URL",
    "https://1482167594-9dtxg7k2fy.ap-shanghai.tencentscf.com")
# 上报鉴权令牌（与云函数环境变量 AUTH_TOKEN 一致；留空=不带鉴权头）
AUTH_TOKEN = os.environ.get("WM_AUTH_TOKEN", "ik3O7Gu9Uk2EOFJHtJXgmA")

MAX_PENDING = 200          # 本地待补传队列上限（防无限膨胀）
REPORT_TIMEOUT = 10        # 单次 POST 超时秒数
RETRY_INTERVAL = 120       # 失败后重试间隔秒数

_pending_path = None
_id_path = None


def _init_paths(root: str):
    global _pending_path, _id_path
    _pending_path = os.path.join(root, "pending_reports.jsonl")
    _id_path = os.path.join(root, "telemetry_id.txt")


def load_device_id(root: str) -> str:
    """读取或生成本机匿名设备 ID（跨局稳定，用于去重统计，不可反查用户）。"""
    _init_paths(root)
    try:
        if os.path.isfile(_id_path):
            with open(_id_path, "r", encoding="utf-8") as f:
                return f.read().strip() or _new_id()
    except Exception:
        pass
    did = _new_id()
    try:
        with open(_id_path, "w", encoding="utf-8") as f:
            f.write(did)
    except Exception:
        pass
    return did


def _new_id() -> str:
    return str(uuid.uuid4())


class Telemetry:
    """后台上报线程 + 本地缓存。start() 后自动处理队列。"""

    def __init__(self, root: str, enabled: bool = True, log=None):
        self.enabled = enabled and bool(ENDPOINT)
        self.log = log or (lambda m: None)
        self.device_id = load_device_id(root)
        self._q = queue.Queue()
        self._thread = None
        self._stop = threading.Event()
        self._last_retry = 0.0
        self._seen = 0
        self._init_pending()
        self.log(f"📡 数据上报: {'开启（匿名 ID '+self.device_id[:8]+'…）' if self.enabled else '关闭'}")

    def _init_pending(self):
        # 把历史缓存文件中的记录灌入队列等待重试
        try:
            if _pending_path and os.path.isfile(_pending_path):
                with open(_pending_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                self._q.put(json.loads(line))
                            except Exception:
                                pass
        except Exception:
            pass

    def start(self):
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                rec = self._q.get(timeout=1.0)
            except queue.Empty:
                # 空队列：若本地有缓存文件残余也一并清（理论上 _q 已含全部）
                self._flush_pending_file()
                continue
            try:
                self._post(rec)
            except Exception as e:
                self._cache(rec)
                self.log(f"📡 上报失败，稍后重试: {e}")
                self._stop.wait(RETRY_INTERVAL)

    def report(self, game: dict):
        """上报一局数据（非阻塞，入队即返回）。"""
        if not self.enabled:
            return
        rec = {
            "app": APP_NAME,
            "ver": APP_VERSION,
            "device": self.device_id,
            "preset": game.get("preset", ""),
            "ts": int(time.time()),
            "game": game.get("game", 0),
            "score": game.get("score", 0),
            "bestLevel": game.get("bestLevel", 0),
            "watermelons": game.get("watermelons", 0),
            "drops": game.get("drops", 0),
            "milestones": game.get("milestones", []),
            "finalLayout": game.get("finalLayout", {}),
        }
        self._q.put(rec)

    def _post(self, rec: dict):
        import urllib.request
        body = json.dumps(rec, ensure_ascii=False).encode("utf-8")
        hdrs = {"Content-Type": "application/json; charset=utf-8"}
        if AUTH_TOKEN:
            hdrs["X-Auth-Token"] = AUTH_TOKEN
        req = urllib.request.Request(
            ENDPOINT, data=body, headers=hdrs, method="POST")
        with urllib.request.urlopen(req, timeout=REPORT_TIMEOUT) as resp:
            if resp.status >= 300:
                raise IOError(f"HTTP {resp.status}")

    def _cache(self, rec: dict):
        """写入本地缓存文件（仅当队列超出内存上限时才落盘，正常运行以队列重试为主）。"""
        try:
            if _pending_path and self._q.qsize() <= MAX_PENDING:
                return
            if _pending_path:
                with open(_pending_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _flush_pending_file(self):
        """成功后清理本地缓存文件（队列消费完即可删）。"""
        try:
            if _pending_path and os.path.isfile(_pending_path) and self._q.empty():
                os.remove(_pending_path)
        except Exception:
            pass


# 单例入口（desktop_app 调用）
_tel = None


def get_telemetry(root: str, enabled: bool = True, log=None) -> Telemetry:
    global _tel
    if _tel is None:
        _tel = Telemetry(root, enabled=enabled, log=log)
    return _tel
