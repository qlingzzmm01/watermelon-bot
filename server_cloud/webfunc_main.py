# -*- coding: utf-8 -*-
"""大西瓜自动化 - 匿名对局数据收数服务（腾讯云 SCF Web 函数版）。

以标准 HTTP server 监听 9000 端口（Web 函数约定），收到 POST / 上报 JSON 后
写入腾讯云 COS（每局一个对象）。运行期仅用标准库，无第三方依赖。

部署方式（云函数控制台）：
  函数类型: Web 函数 | 运行环境: Python 3.9 | 本地上传 zip（本目录打包，含 scf_bootstrap）
  环境变量:
    COS_BUCKET / COS_REGION / COS_SECRET_ID / COS_SECRET_KEY / AUTH_TOKEN
  Web 函数创建完成后会自动生成「函数 URL」（公网 https 直连，无需 API 网关）。
"""
import datetime
import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BUCKET = os.environ.get("COS_BUCKET", "")
REGION = os.environ.get("COS_REGION", "ap-shanghai")
SECRET_ID = os.environ.get("COS_SECRET_ID", "")
SECRET_KEY = os.environ.get("COS_SECRET_KEY", "")
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")


def _cos_auth(method, path, host, now, content_type=""):
    def hmac_sha1(key, msg):
        return hmac.new(key.encode(), msg.encode(), hashlib.sha1).digest()

    key_time = f"{now};{now + 600}"
    sign_key = hmac_sha1(SECRET_KEY, key_time)
    header_str = f"host={host}&content-type={content_type}" if content_type else f"host={host}"
    fmt = f"sha1\n{method}\n{path}\n\n{header_str}\n\n"
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
        headers={"Host": host, "Content-Type": content_type, "Authorization": auth})
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status >= 300:
            raise IOError(f"COS HTTP {resp.status}")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        token = self.headers.get("X-Auth-Token", "")
        return (not AUTH_TOKEN) or token == AUTH_TOKEN

    def do_POST(self):
        if not self._check_auth():
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            rec = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send(400, {"error": "bad json"})
        if not isinstance(rec, dict) or "game" not in rec:
            return self._send(400, {"error": "invalid record"})
        try:
            now = datetime.datetime.now()
            key = (f"games/{now:%Y/%m/%d}/"
                   f"{now:%Y%m%d%H%M%S%f}_{rec.get('device', 'anon')[:8]}.json")
            _put_object(key, json.dumps(rec, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            return self._send(500, {"error": f"storage fail: {e}"})
        return self._send(200, {"ok": True, "key": key})

    def do_GET(self):
        # 健康检查：浏览器直接打开能看到说明部署成功
        return self._send(200, {"ok": True, "service": "wm-telemetry"})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", 9000), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
