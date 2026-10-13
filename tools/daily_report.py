# -*- coding: utf-8 -*-
"""每日自动统计（定时任务入口）：拉 COS 全部数据 → 汇总 → 写当日报告 md。
密钥从 server_cloud/.deploy.json 读取（不入库）。输出到 大西瓜统计报告.md（追加当天段）。"""
import datetime
import json
import os
from collections import Counter

from qcloud_cos import CosConfig, CosS3Client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(ROOT, "server_cloud", ".deploy.json")


def main():
    with open(DEPLOY, encoding="utf-8") as f:
        cfg = json.load(f)
    cos = CosS3Client(CosConfig(Region=cfg["region"], SecretId=cfg["secret_id"],
                                SecretKey=cfg["secret_key"], Scheme="https"))
    r = cos.list_objects(Bucket=cfg["bucket"], Prefix="games/", MaxKeys=1000)
    recs = []
    for c in (r.get("Contents") or []):
        rr = cos.get_object(Bucket=cfg["bucket"], Key=c["Key"])
        recs.append(json.loads(rr["Body"].get_raw_stream().read().decode()))
    reals = [x for x in recs if x.get("device") != "e2e-test-001"]
    vers = Counter(x.get("ver") for x in reals)
    devs = len(set(x["device"] for x in reals))

    now = datetime.datetime.now()
    day = now.strftime("%Y-%m-%d")
    day_recs = [x for x in reals if datetime.datetime.fromtimestamp(x.get("ts", 0)).strftime("%Y-%m-%d") == day]

    def stat(v):
        if not v:
            return "无数据"
        lvs = [x.get("bestLevel", 0) for x in v]
        wm = sum(1 for x in v if x.get("watermelons", 0) > 0)
        sc = sum(x.get("score", 0) for x in v) / len(v)
        return f"{len(v)}局 平均lv{sum(lvs)/len(lvs):.2f} 最高{max(lvs)} 西瓜{wm} 平均分{sc:.0f}"

    lines = [
        f"\n## {now:%Y-%m-%d %H:%M} 自动统计",
        f"- 累计：{len(reals)} 局（按版本 {dict(vers)}），活跃设备 {devs} 台",
        f"- 今日（{day}）：{stat(day_recs)}",
        "- 最新 v1.2.0：" + stat([x for x in reals if x.get("ver") == "1.2.0"]),
        "- 整体：" + stat(reals),
    ]
    out = os.path.join(ROOT, "大西瓜统计报告.md")
    with open(out, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
