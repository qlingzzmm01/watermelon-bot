# -*- coding: utf-8 -*-
"""
从腾讯云 COS 拉取全部对局数据并输出统计（本地运行，需 cos-python-sdk-v5）。

安装：pip install cos-python-sdk-v5
运行：
  set COS_BUCKET=wm-telemetry-125xxxxxxx
  set COS_REGION=ap-shanghai
  set COS_SECRET_ID=AKID...
  set COS_SECRET_KEY=...
  python analyze.py            # 拉取全部并按日聚合
  python analyze.py --dir out  # 只下载 JSON 到 out/ 不统计
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BUCKET = os.environ.get("COS_BUCKET", "")
REGION = os.environ.get("COS_REGION", "ap-shanghai")
SID = os.environ.get("COS_SECRET_ID", "")
SK = os.environ.get("COS_SECRET_KEY", "")


def _client():
    from qcloud_cos import CosConfig, CosS3Client
    return CosS3Client(CosConfig(Region=REGION, SecretId=SID, SecretKey=SK,
                                 Scheme="https", Timeout=30))


def list_keys(client) -> list:
    keys = []
    marker = ""
    while True:
        r = client.list_objects(Bucket=BUCKET, Prefix="games/", Marker=marker, MaxKeys=1000)
        for c in r.get("Contents", []):
            keys.append(c["Key"])
        if r.get("IsTruncated") != "true":
            break
        marker = r.get("NextMarker")
    return keys


def download_all(client, keys, outdir):
    os.makedirs(outdir, exist_ok=True)
    for i, k in enumerate(keys):
        body = client.get_object(Bucket=BUCKET, Key=k)["Body"].get_raw_stream().read()
        name = k.replace("/", "__")
        with open(os.path.join(outdir, name), "wb") as f:
            f.write(body)
        if (i + 1) % 50 == 0:
            print(f"  下载 {i+1}/{len(keys)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="只下载到此目录，不做统计")
    args = ap.parse_args()

    if not (BUCKET and SID and SK):
        print("请设置环境变量 COS_BUCKET/COS_REGION/COS_SECRET_ID/COS_SECRET_KEY")
        sys.exit(1)
    client = _client()
    keys = list_keys(client)
    print(f"COS 中共 {len(keys)} 局数据")

    if args.dir:
        download_all(client, keys, args.dir)
        print("下载完成")
        return

    # 统计（流式解析，不落盘）
    recs = []
    for k in keys:
        try:
            body = client.get_object(Bucket=BUCKET, Key=k)["Body"].get_raw_stream().read()
            recs.append(json.loads(body.decode("utf-8")))
        except Exception:
            continue

    n = len(recs)
    if n == 0:
        print("无数据")
        return
    from collections import Counter
    by_preset = {}
    for r in recs:
        p = r.get("preset", "?")
        d = by_preset.setdefault(p, [])
        d.append(r)
    print(f"\n共 {n} 局 | 设备数 {len(set(r.get('device','') for r in recs))}")
    print(f"西瓜局(watermelons>0): {sum(1 for r in recs if r.get('watermelons',0)>0)} "
          f"({sum(1 for r in recs if r.get('watermelons',0)>0)/n*100:.1f}%)")
    print("\n=== 按策略 ===")
    for p, rs in sorted(by_preset.items(), key=lambda x: -len(x[1])):
        lvs = [r.get("bestLevel", 0) for r in rs]
        wm = sum(r.get("watermelons", 0) for r in rs)
        avg = sum(lvs) / len(lvs)
        print(f"  {p:10s} n={len(rs):3d} 平均lv={avg:.2f} 西瓜={wm} "
              f"({sum(1 for r in rs if r.get('watermelons',0)>0)/len(rs)*100:.0f}%)")
    # 断链分析：milestones 里 last 等级分布（死亡时最高合成等级）
    print("\n=== 断链分析（每局 milestones 末位等级分布）===")
    cnt = Counter()
    for r in recs:
        ms = r.get("milestones") or []
        cnt[ms[-1]["lv"] if ms else 0] += 1
    for lv in sorted(cnt):
        print(f"  最高到 lv{lv}: {cnt[lv]} 局")


if __name__ == "__main__":
    main()
