# -*- coding: utf-8 -*-
"""
腾讯云函数 SCF —— 大西瓜自动化对局数据收数服务（零服务器成本）。

功能：
  接收客户端 telemetry.py 上报的每局 JSON，写入腾讯云 COS 对象存储。
  每局一个对象：games/YYYY/MM/YYYYMMDD_<seq>.json（免费额度内成本可忽略）。

部署步骤（详见同目录 README.md）：
  1. 开通 COS：创建存储桶（如 wm-telemetry-125xxxxxxx），地域如 ap-shanghai。
  2. 创建云函数：运行时 Python 3.9+，本文件上传；配置环境变量：
       COS_BUCKET=<桶名>            e.g. wm-telemetry-1250000000
       COS_REGION=ap-shanghai
       COS_SECRET_ID=<子账号密钥ID>   （最小权限：仅 PutObject 该桶）
       COS_SECRET_KEY=<密钥>
       AUTH_TOKEN=<自定义令牌>        （客户端 telemetry 须带 X-Auth-Token 相同值）
  3. 创建触发器：API 网关触发（默认域名即可，无需备案），发布，得到 URL。
  4. 把该 URL 填回客户端 telemetry.py 的 ENDPOINT（或环境变量 WM_TELEMETRY_URL）。
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import time

BUCKET = os.environ.get("COS_BUCKET", "")
REGION = os.environ.get("COS_REGION", "ap-shanghai")
SECRET_ID = os.environ.get("COS_SECRET_ID", "")
SECRET_KEY = os.environ.get("COS_SECRET_KEY", "")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")


def _cos_auth(method, path, host, now, content_type=""):
    """COS 签名（COS XML API V5，签名算法见官方文档）。"""
    def hmac_sha1(key, msg):
        return hmac.new(key.encode(), msg.encode(), hashlib.sha1).digest()

    key_time = f"{now};{now + 600}"
    sign_key = hmac_sha1(SECRET_KEY, key_time)
    fmt = ("sha1\n{method}\n{uri}\n\n{header_str}\n\n".format(
        method=method, uri=path,
        header_str=f"host={host}&content-type={content_type}" if content_type
        else f"host={host}"))
    signature = hmac_sha1(sign_key, fmt).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={SECRET_ID}&q-sign-time={key_time}"
            f"&q-key-time={key_time}&q-header-list={'content-type;' if content_type else ''}"
            f"host&q-url-param-list=&q-signature={signature}")


def _put_object(key: str, body: bytes, content_type: str = "application/json"):
    import urllib.request
    host = f"{BUCKET}.cos.{REGION}.myqcloud.com"
    now = int(time.time())
    auth = _cos_auth("PUT", "/" + key, host, now, content_type)
    req = urllib.request.Request(
        f"https://{host}/{key}", data=body, method="PUT",
        headers={
            "Host": host,
            "Content-Type": content_type,
            "Authorization": auth,
        })
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise IOError(f"COS HTTP {resp.status}")


def main_handler(event, context):
    """SCF 入口。event 结构见 API 网关触发格式。"""
    # 鉴权（简单令牌，防止陌生人灌数据）
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if AUTH_TOKEN and headers.get("x-auth-token") != AUTH_TOKEN:
        return _resp(401, {"error": "unauthorized"})

    try:
        body = event.get("body") or "{}"
        if isinstance(body, (bytes, bytearray)):
            body = bytes(body).decode("utf-8")
        if event.get("isBase64Encoded"):
            import base64
            body = base64.b64decode(body).decode("utf-8")
        rec = json.loads(body)
    except Exception:
        return _resp(400, {"error": "bad json"})

    # 基本字段校验（丢弃异常数据，防止脏数据入库）
    if not isinstance(rec, dict) or "game" not in rec:
        return _resp(400, {"error": "invalid record"})

    try:
        # 每局一个对象：按天分目录 + 毫秒时间戳，避免覆盖
        now = datetime.datetime.now()
        key = (f"games/{now:%Y/%m/%d}/"
               f"{now:%Y%m%d%H%M%S%f}_{rec.get('device','anon')[:8]}.json")
        _put_object(key, json.dumps(rec, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        return _resp(500, {"error": f"storage fail: {e}"})

    return _resp(200, {"ok": True, "key": key})


def _resp(code: int, obj: dict):
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(obj, ensure_ascii=False),
        "isBase64Encoded": False,
    }
