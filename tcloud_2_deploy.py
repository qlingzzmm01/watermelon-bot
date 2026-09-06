# -*- coding: utf-8 -*-
"""部署步骤 2：建 COS 私有桶 + 创建 SCF 函数。
用法: python tcloud_2_deploy.py <SecretId> <SecretKey>
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import time

from qcloud_cos import CosConfig, CosS3Client
from tencentcloud.common import credential
from tencentcloud.scf.v20180416 import scf_client, models as scf_models

SID, SK = sys.argv[1], sys.argv[2]
REGION = "ap-shanghai"
APPID = "1482167594"   # 账号 AppId（list_buckets 实际返回；UIN 100052606754 ≠ AppId）
BUCKET = f"wm-telemetry-{APPID}"
AUTH_TOKEN = secrets.token_urlsafe(16)

# ---------- 1. 建 COS 桶（私有） ----------
config = CosConfig(Region=REGION, SecretId=SID, SecretKey=SK, Scheme="https")
cos = CosS3Client(config)
try:
    cos.create_bucket(Bucket=BUCKET)
    print(f"✅ COS 桶已建: {BUCKET}")
except Exception as e:
    msg = str(e)
    if "BucketAlreadyOwnedByYou" in msg or "exists" in msg.lower():
        print(f"ℹ️ COS 桶已存在: {BUCKET}")
    else:
        print(f"❌ 建桶失败: {msg[:200]}"); sys.exit(1)
cos.put_bucket_acl(Bucket=BUCKET, ACL="private")
print("✅ 桶 ACL=private")

# ---------- 2. 打包 index.py 为真 zip ----------
import io
import zipfile
code_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_cloud")
code_path = os.path.join(code_dir, "index.py")
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write(code_path, "index.py")
code_zip = base64.b64encode(buf.getvalue()).decode()

# ---------- 3. 创建 SCF 函数 ----------
cre = credential.Credential(SID, SK)
client = scf_client.ScfClient(cre, REGION)
# 先删旧函数（若状态异常残留 CreateFailed，否则无法重建）
try:
    dreq = scf_models.DeleteFunctionRequest()
    dreq.FunctionName = "wm-telemetry"
    client.DeleteFunction(dreq)
    print("ℹ️ 已删除旧函数，等待 8s 重建…")
    time.sleep(8)
except Exception:
    pass
env = [
    {"Key": "COS_BUCKET", "Value": BUCKET},
    {"Key": "COS_REGION", "Value": REGION},
    {"Key": "COS_SECRET_ID", "Value": SID},
    {"Key": "COS_SECRET_KEY", "Value": SK},
    {"Key": "AUTH_TOKEN", "Value": AUTH_TOKEN},
]
req = scf_models.CreateFunctionRequest()
req.FunctionName = "wm-telemetry"
req.Handler = "index.main_handler"
req.Runtime = "Python3.9"
req.MemorySize = 128
req.Timeout = 10
_code = scf_models.Code()
_code.ZipFile = code_zip
req.Code = _code
_env = scf_models.Environment()
_env.Variables = env
req.Environment = _env
req.Role = "SCF_QcsRole"
try:
    resp = client.CreateFunction(req)
    print(f"✅ 函数已创建/更新: wm-telemetry")
except Exception as e:
    print(f"❌ 创建函数失败: {str(e)[:300]}"); sys.exit(1)

# 写本地配置供后续步骤使用
info = {"bucket": BUCKET, "region": REGION, "auth_token": AUTH_TOKEN,
        "secret_id": SID, "secret_key": SK}
cfg = os.path.join(code_dir, ".deploy.json")
with open(cfg, "w", encoding="utf-8") as f:
    json.dump(info, f, ensure_ascii=False, indent=2)
print(f"🔑 AUTH_TOKEN={AUTH_TOKEN}")
print(f"📝 部署信息已存: server_cloud/.deploy.json")
print("下一步：创建 API 网关触发器（步骤 3）")
