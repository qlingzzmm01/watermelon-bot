# -*- coding: utf-8 -*-
"""高潜力局分析：统计每局 lv10 达成投数与 lv11 相关性。
用于判断「lv10 达成越早 → 越可能出 lv11」的假设，以及给「激进模式」提供阈值。

用法: python tools/analyze_potential.py [ver=1.2.0]
密钥从 server_cloud/.deploy.json 读取（该文件不入库）。
"""
import json
import os
import sys
import time

from qcloud_cos import CosConfig, CosS3Client

BUCKET = "wm-telemetry-1482167594"
VER = sys.argv[1] if len(sys.argv) > 1 else "1.2.0"
_DEPLOY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "server_cloud", ".deploy.json")
with open(_DEPLOY, encoding="utf-8") as _f:
    _cfg = json.load(_f)
config = CosConfig(Region=_cfg.get("region", "ap-shanghai"),
                   SecretId=_cfg["secret_id"], SecretKey=_cfg["secret_key"], Scheme="https")
cos = CosS3Client(config)


def main():
    r = cos.list_objects(Bucket=BUCKET, Prefix="games/", MaxKeys=1000)
    recs = []
    for c in (r.get("Contents") or []):
        rr = cos.get_object(Bucket=BUCKET, Key=c["Key"])
        x = json.loads(rr["Body"].get_raw_stream().read().decode())
        if x.get("device") != "e2e-test-001" and x.get("ver") == VER:
            recs.append(x)

    if not recs:
        print(f"v{VER} 无数据")
        return

    # 每局统计：达成 lv10 时的投数（milestones 里 lv==10 的 atDrop）
    recs.sort(key=lambda x: x.get("ts", 0))
    lv10_drops, wm, no_lv10 = [], 0, 0
    for x in recs:
        ms = {m.get("lv"): m.get("atDrop") for m in (x.get("milestones") or [])}
        d10 = ms.get(10)
        if d10 is None:
            no_lv10 += 1
            continue
        lv10_drops.append(d10)
        if x.get("watermelons", 0) > 0:
            wm += 1

    print(f"=== v{VER} 共 {len(recs)} 局 ===")
    print(f"达成 lv10：{len(lv10_drops)} 局；未达成 lv10：{no_lv10} 局；出西瓜 {wm} 局")
    if lv10_drops:
        avg = sum(lv10_drops) / len(lv10_drops)
        print(f"lv10 达成平均投数：{avg:.1f}（最快 {min(lv10_drops)}，最慢 {max(lv10_drops)}）")
        # 假设验证：lv10 达成越早是否越可能出西瓜
        early = [x for x in recs
                 if (ms := {m.get('lv'): m.get('atDrop') for m in (x.get('milestones') or [])}).get(10)
                 and ms[10] <= avg]
        late = [x for x in recs
                if (ms := {m.get('lv'): m.get('atDrop') for m in (x.get('milestones') or [])}).get(10)
                and ms[10] > avg]
        we = sum(1 for x in early if x.get("watermelons", 0) > 0)
        wl = sum(1 for x in late if x.get("watermelons", 0) > 0)
        print(f"假设「lv10 越早越易出西瓜」：")
        print(f"  早于均值(≤{avg:.0f}投) {len(early)} 局 → 西瓜率 {we}/{len(early)}"
              f" ({100*we/max(1,len(early)):.0f}%)")
        print(f"  晚于均值(>{avg:.0f}投) {len(late)} 局 → 西瓜率 {wl}/{len(late)}"
              f" ({100*wl/max(1,len(late)):.0f}%)")


if __name__ == "__main__":
    main()
