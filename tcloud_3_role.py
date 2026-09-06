# -*- coding: utf-8 -*-
"""创建 SCF_QcsRole 服务角色（SCF 首次部署需要）。
用法: python tcloud_3_role.py <SecretId> <SecretKey>"""
import json
import sys
from tencentcloud.common import credential
from tencentcloud.cam.v20190116 import cam_client, models as cam_models

sid, sk = sys.argv[1], sys.argv[2]
cre = credential.Credential(sid, sk)
client = cam_client.CamClient(cre, "ap-shanghai")

# 1. 创建角色（信任 scf.qcloud.com）
policy = {
    "version": "2.0",
    "statement": [{
        "action": "sts:AssumeRole",
        "effect": "allow",
        "principal": {"service": ["scf.qcloud.com"]},
    }]
}
req = cam_models.CreateRoleRequest()
req.RoleName = "SCF_QcsRole"
req.PolicyDocument = json.dumps(policy)
req.Description = "SCF 默认服务角色（自动创建）"
try:
    resp = client.CreateRole(req)
    print(f"✅ 角色已建 roleId={resp.RoleId}")
except Exception as e:
    if "RoleNameInUse" in str(e) or "exists" in str(e).lower():
        print("ℹ️ 角色已存在")
    else:
        print("❌ 建角色失败:", str(e)[:200]); sys.exit(1)

# 2. 关联策略（SCF 访问其它云资源所需，预置策略 ID）
try:
    req2 = cam_models.AttachRolePolicyRequest()
    req2.PolicyId = 302008  # QcloudAccessForScfRole 预置策略 ID
    req2.AttachRoleName = "SCF_QcsRole"
    client.AttachRolePolicy(req2)
    print("✅ 已绑定 QcloudAccessForScfRole(302008)")
except Exception as e:
    print("⚠️ 绑策略: ", str(e)[:150])
print("SCF_QcsRole 就绪")
