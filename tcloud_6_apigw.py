# -*- coding: utf-8 -*-
"""API 网关直连 SCF（绕过 SCF 触发管理不支持问题）。
用法: python tcloud_6_apigw.py <SecretId> <SecretKey>"""
import sys
from tencentcloud.common import credential
from tencentcloud.apigateway.v20180808 import apigateway_client, models

sid, sk = sys.argv[1], sys.argv[2]
REGION = "ap-shanghai"
cre = credential.Credential(sid, sk)
gw = apigateway_client.ApigatewayClient(cre, REGION)

# 1. 创建 API 服务（公网 HTTPS）
try:
    sq = models.CreateServiceRequest()
    sq.ServiceName = "wm-telemetry"
    sq.Protocol = "https"
    sq.ServiceDesc = "大西瓜自动化数据收数"
    sq.NetTypes = ["outer"]
    r = gw.CreateService(sq)
    svc_id = r.ServiceId
    print(f"✅ 服务已建: {svc_id} (子域名 {r.SubDomain})")
except Exception as e:
    print(f"❌ 建服务失败: {str(e)[:250]}"); sys.exit(1)

# 2. 创建 API：POST /report -> SCF wm-telemetry
try:
    aq = models.CreateApiRequest()
    aq.ServiceId = svc_id
    aq.ApiName = "report"
    aq.ApiDesc = "接收对局上报"
    aq.Protocol = "https"
    aq.AuthType = "APP"            # 免鉴权需配合 UsagePlan? 用 NONE
    aq.ApiBusinessType = "NORMAL"
    aq.RequestConfig = models.RequestConfig()
    aq.RequestConfig.Method = "POST"
    aq.RequestConfig.Path = "/report"
    aq.ServiceType = "SCF"
    aq.ServiceScfFunctionName = "wm-telemetry"
    aq.ServiceScfFunctionNamespace = "default"
    aq.ServiceScfFunctionQualifier = "$LATEST"
    aq.ServiceScfIsIntegratedResponse = True
    aq.ServiceTimeout = 10
    aq.IsBase64Encoded = False
    r2 = gw.CreateApi(aq)
    api_id = r2.ApiId if hasattr(r2, "ApiId") else r2.Result.ApiId
    print(f"✅ API 已建: {api_id}")
except Exception as e:
    print(f"❌ 建 API 失败: {str(e)[:300]}"); sys.exit(1)

# 3. 发布到 release 环境
try:
    rq = models.ReleaseServiceRequest()
    rq.ServiceId = svc_id
    rq.EnvironmentName = "release"
    rq.ReleaseDesc = "v1"
    gw.ReleaseService(rq)
    print("✅ 已发布到 release")
except Exception as e:
    print(f"❌ 发布失败: {str(e)[:200]}"); sys.exit(1)

# 4. 拿访问域名
try:
    dq = models.DescribeServiceRequest()
    dq.ServiceId = svc_id
    d = gw.DescribeService(dq)
    sub = d.SubDomain
    print(f"\n🌐 上报 URL: https://{sub}/report")
    print(f"   完整:  https://{sub}/release/report (带环境前缀) 或上面直连")
except Exception as e:
    print("查域名失败:", str(e)[:150])
