# -*- coding: utf-8 -*-
"""部署步骤 3：为 wm-telemetry 函数创建 API 网关触发器并发布，输出公网 URL。
用法: python tcloud_4_trigger.py <SecretId> <SecretKey>"""
import json
import sys
from tencentcloud.common import credential
from tencentcloud.scf.v20180416 import scf_client, models as scf_models
from tencentcloud.apigateway.v20180808 import apigateway_client, models as gw_models

sid, sk = sys.argv[1], sys.argv[2]
REGION = "ap-shanghai"
cre = credential.Credential(sid, sk)

# 1. 用 SCF SDK 创建 API 网关触发器（type=apigw 自动建服务）
client = scf_client.ScfClient(cre, REGION)
req = scf_models.CreateTriggerRequest()
req.FunctionName = "wm-telemetry"
req.TriggerName = "wm-telemetry-apigw"
req.Type = "apigw"
req.TriggerDesc = json.dumps({"api": {"authRequired": "FALSE",
                                       "requestConfig": {"method": "ANY", "path": "/"},
                                       "isIntegratedResponse": "TRUE"},
                               "service": {"serviceName": "wm-telemetry"}})
try:
    resp = client.CreateTrigger(req)
    print("✅ 触发器已创建 trigger=", resp.TriggerInfo.TriggerName)
    print("   desc:", str(resp.TriggerInfo.Description)[:200])
except Exception as e:
    print("❌ 建触发器失败:", str(e)[:400])
    print("→ 请在控制台 https://console.cloud.tencent.com/scf/list-detail/wm-telemetry/ap-shanghai/function 手动创建触发器")
    sys.exit(1)

# 2. 通过 apigateway SDK 查服务与发布（拿公网 URL）
gw = apigateway_client.ApigatewayClient(cre, REGION)
# 列出 API 网关服务
try:
    lq = gw_models.DescribeServicesStatusRequest()
    lq.Limit = 50
    lst = gw.DescribeServicesStatus(lq)
    for svc in (lst.Result or {}).get("ServiceSet", []) if isinstance(lst.Result, dict) else []:
        if "wm-telemetry" in (svc.get("ServiceName") or "") or "wm" in (svc.get("ServiceName") or "").lower():
            print(f"服务: id={svc.get('ServiceId')} name={svc.get('ServiceName')} "
                  f"outer={svc.get('OuterSubDomain')} url={svc.get('UsagePlanList')}")
except Exception as e:
    print("查询服务:", str(e)[:200])
