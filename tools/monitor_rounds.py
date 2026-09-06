# -*- coding: utf-8 -*-
"""监控 bot 跑满 N 局，汇总每局战绩后写结果文件。"""
import json, time, urllib.request, sys

N = int(sys.argv[1]) if len(sys.argv) > 1 else 3
OUT = r"C:\Users\Administrator\Desktop\大西瓜\rounds_result.txt"
URL = "http://127.0.0.1:8733/api/status"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过代理
DEADLINE = time.time() + 40 * 60      # 最多等 40 分钟

seen = 0
while time.time() < DEADLINE:
    try:
        d = json.loads(opener.open(URL, timeout=8).read().decode("utf-8"))
    except Exception as e:
        print(f"[monitor] 查询失败: {e}")
        time.sleep(10)
        continue
    hist = d.get("gameHistory") or []
    cur = d.get("gameNo") or 0
    if len(hist) > seen:
        for g in hist[seen:]:
            print(f"[monitor] 第 {g['game']} 局完成: 最高lv{g['bestLevel']} "
                  f"大西瓜×{g['watermelons']} 得分{g['score']} 投放{g['drops']}")
        seen = len(hist)
    else:
        print(f"[monitor] 进行中 第{cur}局 drops={d.get('drops')} "
              f"本局最高lv{d.get('bestLevelGame')} status={d.get('status')}")
    if len(hist) >= N:
        lines = [f"=== {len(hist)} 局完成 ===", ""]
        lines.append(f"{'局':<5}{'最高等级':>9}{'大西瓜数':>10}{'得分':>9}{'投放数':>9}")
        tot11 = 0
        for g in hist:
            lines.append(f"{g['game']:<5}{('lv'+str(g['bestLevel'])):>9}"
                         f"{g['watermelons']:>10}{g['score']:>9}{g['drops']:>9}")
            tot11 += g["watermelons"]
        lvs = [g["bestLevel"] for g in hist]
        lines.append("")
        lines.append(f"平均最高等级: lv{sum(lvs)/len(lvs):.1f}   最好: lv{max(lvs)}   "
                     f"大西瓜合计: {tot11}")
        txt = "\n".join(lines)
        open(OUT, "w", encoding="utf-8").write(txt)
        print("\n" + txt)
        break
    time.sleep(15)
else:
    print("[monitor] 超时未完成")
    open(OUT, "w", encoding="utf-8").write("超时未完成")
