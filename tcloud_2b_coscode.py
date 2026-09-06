# -*- coding: utf-8 -*-
"""部署 2b：代码传 COS + SCF 函数引用 COS 创建（比 ZipFile 可靠）。
用法: python tcloud_2b_coscode.py <SecretId> <SecretKey>"""
import io
import json
import os
import secrets
import sys
import time
import zipfile

from qcloud_cos import CosConfig, CosS3Client
from tencentcloud.common import credential
from tencentcloud.scf.v20180416 import scf_client, models as scf_models

SID, SK = sys.argv[1], sys.argv[2]
REGION = "ap-shanghai"
APPID = "1482167594"
BUCKET = f"wm-telemetry-{APPID}"
AUTH_TOKEN = secrets.token_urlsafe(16)
NS = "default"

# 1. index.py -> zip -> 上传 COS
config = CosConfig(Region=REGION, SecretId=SID, SecretKey=SK, Scheme="https")
cos = CosS3Client(config)
code_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_cloud")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(os.path.join(code_dir, "index.py"), "index.py")
cos.put_object(Bucket=BUCKET, Key="_code/wm-telemetry.zip", Body=buf.getvalue())
print(f"✅ 代码已传 COS: _code/wm-telemetry.zip ({len(buf.getvalue())}B)")

# 2. 删旧函数
cre = credential.Credential(SID, SK)
scf = scf_client.ScfClient(cre, REGION)
try:
    d = scf_models.DeleteFunctionRequest(); d.FunctionName = "wm-telemetry"
    scf.DeleteFunction(d); print("ℹ️ 已删旧函数"); time.sleep(6)
except Exception:
    pass

# 3. 用 COS 代码创建
req = scf_models.CreateFunctionRequest()
req.FunctionName = "wm-telemetry"
req.Handler = "index.main_handler"
req.Runtime = "Python3.9"
req.MemorySize = 128
req.Timeout = 10
req.Namespace = NS
req.Description = "大西瓜自动化 - 匿名对局数据收数"
_code = scf_models.Code()
_code.CosBucketName = BUCKET
_code.CosBucketRegion = REGION
_code.CosObjectName = "_code/wm-telemetry.zip"
req.Code = _code
_env = scf_models.Environment()
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
    resp = scf.CreateFunction(req)
    print("✅ 函数创建请求已受理，等待状态就绪…")
except Exception as e:
    print("❌ 创建失败:", str(e)[:300]); sys.exit(1)

# 4. 轮询状态
for i in range(12):
    time.sleep(5)
    try:
        g = scf_models.GetFunctionRequest(); g.FunctionName = "wm-telemetry"; g.Namespace = NS
        r = scf.GetFunction(g)
        st = r.Status
        print(f"  [{i*5+5}s] status={st}")
        if st == "Active":
            print(f"✅ 函数就绪！ AUTH_TOKEN={AUTH_TOKEN}")
            info = {"bucket": BUCKET, "region": REGION, "auth_token": AUTH_TOKEN,
                    "secret_id": SID, "secret_key": SK}
            with open(os.path.join(code_dir, ".deploy.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            sys.exit(0)
        if st in ("CreateFailed", "UpdateFailed"):
            print("❌ 函数状态失败"); sys.exit(1)
    except Exception as e:
        print("  查询:", str(e)[:100])
print("⚠️ 超时未 Active，请到控制台查看"); sys.exit(1)
