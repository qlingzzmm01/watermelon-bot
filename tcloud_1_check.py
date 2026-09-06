# -*- coding: utf-8 -*-
"""部署步骤 1：验证密钥 + 查 AppId。用法: python tcloud_1_check.py <SecretId> <SecretKey>"""
import sys
from tencentcloud.common import credential
from tencentcloud.sts.v20180813 import sts_client, models as sts_models

sid, sk = sys.argv[1], sys.argv[2]
cre = credential.Credential(sid, sk)
for region in ("ap-shanghai", "ap-guangzhou", "ap-beijing"):
    try:
        client = sts_client.StsClient(cre, region)
        req = sts_models.GetCallerIdentityRequest()
        resp = client.GetCallerIdentity(req)
        print(f"✅ region={region} account={resp.AccountId} arn={resp.Arn}")
        sys.exit(0)
    except Exception as e:
        print(f"region={region} 失败: {str(e)[:120]}")
sys.exit(1)
