# -*- coding: utf-8 -*-
"""给密钥所属用户授予 SCF 部署所需权限（CLS 只读 + SCF 全量 + COS 只读）。
若密钥是子账号 → AttachUserPolicy；若是主账号密钥（自身全权限）则无需。
用法: python tcloud_5_grant.py <SecretId> <SecretKey>"""
import json
import sys
from tencentcloud.common import credential
from tencentcloud.cam.v20190116 import cam_client, models

sid, sk = sys.argv[1], sys.argv[2]
cre = credential.Credential(sid, sk)
client = cam_client.CamClient(cre, "ap-shanghai")

# 1. 查当前密钥身份（主账号 or 子账号）
try:
    req = models.GetUserAppIdRequest()
    r = client.GetUserAppId(req)
    print(f"AppId={r.AppId} Uin={r.Uin} OwnerUin={r.OwnerUin}")
except Exception as e:
    print("身份查询:", str(e)[:150])

# 2. 找「用户」——如果是子账号，把策略绑到子账号；主账号用策略默认全权限
# 简化：直接创建一个自定义策略并绑定到当前调用者，需要知道 username。
# 用 ListUsers 找名称匹配（若 root 则 ListUsers 返回空，跳过）
try:
    lq = models.ListUsersRequest()
    users = client.ListUsers(lq)
    for u in (users.Data or []):
        print(f"用户: uid={u.Uid} name={u.Name}")
except Exception as e:
    print("列用户:", str(e)[:150])
