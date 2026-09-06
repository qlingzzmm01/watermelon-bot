# 数据收数服务部署说明（腾讯云函数 SCF + COS，零服务器成本）

客户端每局结束后匿名上报 1 条 JSON，由本云函数写入 COS。免费额度内 0 元。

## 一、开通 COS（对象存储）

1. 控制台 → 对象存储 COS → 创建存储桶
   - 名称：如 `wm-telemetry`（自动带账号后缀 `wm-telemetry-125xxxxxxx`）
   - 地域：`华东(上海)` 等（影响上报延迟，选离你近的）
   - 访问权限：**私有读写**
2. 建议用子账号最小权限（可选但推荐）：
   - 访问管理 CAM → 用户 → 新建子用户（编程访问）→ 授权策略里只加：
     `QcloudCOSDataFullControl` 或自定义仅 `PutObject` 于该桶的桶策略

## 二、部署云函数（SCF）

1. 控制台 → 云函数 SCF → 新建
   - 创建方式：**从头开始**，运行环境 **Python 3.9**
   - 函数名：`wm-telemetry`
   - 提交方法：**本地上传 zip**（把 `index.py` 打成 zip 上传，无需任何第三方依赖）
2. 函数配置 → 环境变量，填 5 个：
   ```
   COS_BUCKET      wm-telemetry-125xxxxxxx   （你桶的完整名称）
   COS_REGION      ap-shanghai               （桶所在地域代码）
   COS_SECRET_ID   AKID…                      （子账号/主账号 SecretId）
   COS_SECRET_KEY  …                          （对应 SecretKey）
   AUTH_TOKEN      自己设一串随机字符串        （客户端鉴权令牌）
   ```
   （若用主账号密钥，权限最大但最简单；介意安全就用子账号。）
3. 函数配置 → 内存 128MB、超时 10s 即可。
4. 创建触发器 → **API 网关触发**：
   - 当前 API：新建 API 服务
   - 路径：`/` 或 `/report`；请求方法：**POST**；发布环境：**发布**
   - 完成后得到访问地址，形如 `https://service-xxxx-125xxxxxxx.ap-shanghai.tencentapigw.com/report`
   - ⚠ 该地址是平台默认域名，**无需备案**即可被公网调用

## 三、客户端接上端点

编辑客户端 `telemetry.py` 顶部：

```python
ENDPOINT = os.environ.get("WM_TELEMETRY_URL", "")
```

改为（或打包前直接填死；AUTH_TOKEN 同一位置新增）：

```python
import os
ENDPOINT = os.environ.get("WM_TELEMETRY_URL", "https://service-xxxx.ap-shanghai.tencentapigw.com/report")
AUTH_TOKEN = os.environ.get("WM_AUTH_TOKEN", "你设置的令牌")
```

telemetry.py 发送时需带请求头 `X-Auth-Token: <令牌>`。客户端为源码分发时建议把令牌也做成可配置（当前 ENV 方式已支持：`WM_TELEMETRY_URL` / `WM_AUTH_TOKEN`）。

## 四、验证

本地测试（无鉴权时）：
```
curl -X POST https://service-xxxx.ap-shanghai.tencentapigw.com/report \
  -H "Content-Type: application/json" -H "X-Auth-Token: 你的令牌" \
  -d '{"app":"大西瓜自动化","ver":"1.0.0","device":"test","preset":"default","ts":1699999999,"game":1,"score":100,"bestLevel":10,"watermelons":0,"drops":120,"milestones":[],"finalLayout":{}}'
```
返回 `{"ok": true, ...}` 后，去 COS 桶看 `games/2026/09/06/...json` 是否存在。

## 五、日常分析

COS 数据太多时用 `analyze.py` 或官方 COS 清单批量下载后本地分析：
- 西瓜率 = watermelons>0 的记录占比
- 按 preset 分组比平均 bestLevel / 得分
- milestones 聚合找「断链等级」（某等级后长时间无下一级 = 策略瓶颈）
