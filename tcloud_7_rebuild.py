# -*- coding: utf-8 -*-
"""一键重建 wm-telemetry 事件函数 + http 触发（函数 URL）。
用法: python tcloud_7_rebuild.py <SecretId> <SecretKey>"""
import io
import json
import os
import secrets
import sys
import time
import zipfile

from tencentcloud.common import credential
from tencentcloud.scf.v20180416 import scf_client, models

SID, SK = sys.argv[1], sys.argv[2]
REGION = "ap-shanghai"
APPID = "1482167594"
BUCKET = f"wm-telemetry-{APPID}"
AUTH_TOKEN = secrets.token_urlsafe(16)

cre = credential.Credential(SID, SK)
scf = scf_client.ScfClient(cre, REGION)

# 1. 打包 index.py
import base64
code_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_cloud")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(os.path.join(code_dir, "index.py"), "index.py")
code_zip = base64.b64encode(buf.getvalue()).decode()

# 2. 删旧函数（若存在）
try:
    d = models.DeleteFunctionRequest(); d.FunctionName = "wm-telemetry"; d.Namespace = "default"
    scf.DeleteFunction(d); print("ℹ️ 删除旧函数"); time.sleep(8)
except Exception:
    pass

# 3. 创建事件函数
req = models.CreateFunctionRequest()
req.FunctionName = "wm-telemetry"
req.Handler = "index.main_handler"
req.Runtime = "Python3.9"
req.MemorySize = 128
req.Timeout = 10
req.Namespace = "default"
_code = models.Code(); _code.ZipFile = code_zip
req.Code = _code
_env = models.Environment()
_env.Variables = [
    {"Key": "COS_BUCKET", "Value": BUCKET},
    {"Key": "COS_REGION", "Value": REGION},
    {"Key": "COS_SECRET_ID", "Value": SID},
    {"Key": "COS_SECRET_KEY", "Value": SK},
    {"Key": "AUTH_TOKEN", "Value": AUTH_TOKEN},
]
req.Environment = _env
req.Role = "SCF_QcsRole"
try:
    scf.CreateFunction(req); print("✅ 函数创建受理")
except Exception as e:
    print("❌ 创建失败:", str(e)[:300]); sys.exit(1)

# 4. 轮询状态
ok = False
for i in range(12):
    time.sleep(5)
    g = models.GetFunctionRequest(); g.FunctionName = "wm-telemetry"; g.Namespace = "default"
    r = scf.GetFunction(g)
    print(f"  [{i*5+5}s] {r.Status}")
    if r.Status == "Active":
        ok = True; break
    if r.Status == "CreateFailed":
        import json as J
        j = J.loads(r.to_json_string())
        for sr in j.get("StatusReasons", []):
            print("  失败原因:", (sr.get("ErrorMessage") or "")[:300])
        sys.exit(1)
if not ok:
    print("⚠️ 超时"); sys.exit(1)

# 5. 创建 http 触发（函数 URL）
time.sleep(3)
tr = models.CreateTriggerRequest()
tr.FunctionName = "wm-telemetry"; tr.Namespace = "default"
tr.TriggerName = "wm-http"
tr.Type = "http"
tr.TriggerDesc = json.dumps({"authType": "NONE", "method": "POST", "path": "/report"})
try:
    r2 = scf.CreateTrigger(tr)
    print("✅ http 触发已建")
    print("  info:", r2.TriggerInfo.to_json_string()[:300])
except Exception as e:
    print("⚠️ http 触发创建失败:", str(e)[:250])

# 6. 保存配置
json.dump({"bucket": BUCKET, "region": REGION, "auth_token": AUTH_TOKEN,
           "secret_id": SID, "secret_key": SK},
          open(os.path.join(code_dir, ".deploy.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print(f"🔑 AUTH_TOKEN={AUTH_TOKEN}")
