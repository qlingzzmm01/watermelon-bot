# -*- coding: utf-8 -*-
"""GitHub Trees API 全量推送（force，含新 commit）。
用法: python gh_push.py <token> <commit_msg>"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

TOKEN = sys.argv[1]
MSG = sys.argv[2] if len(sys.argv) > 2 else "update"
REPO = "qlingzzmm01/watermelon-bot"
ROOT = os.path.dirname(os.path.abspath(__file__))


def api(method, url, payload=None):
    req = urllib.request.Request(
        url, method=method,
        headers={"Authorization": f"Bearer {TOKEN}",
                 "Accept": "application/vnd.github+json"})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main():
    files = subprocess.run(["git", "ls-files", "-z"], capture_output=True,
                           text=True, cwd=ROOT).stdout.split("\0")
    files = [f for f in files if f]
    print(f"共 {len(files)} 文件")

    # 获取远端 HEAD + base tree
    _, d = api("GET", f"https://api.github.com/repos/{REPO}/git/ref/heads/main")
    head = d.get("object", {}).get("sha")
    if not head:
        print("远端无 main:", d); sys.exit(1)
    _, cd = api("GET", f"https://api.github.com/repos/{REPO}/git/commits/{head}")
    base_tree = cd.get("tree", {}).get("sha")

    # 上传所有文件为 blob
    items = []
    for f in files:
        p = os.path.join(ROOT, f)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as fh:
            content = base64.b64encode(fh.read()).decode()
        st, bd = api("POST", f"https://api.github.com/repos/{REPO}/git/blobs",
                     {"content": content, "encoding": "base64"})
        if st >= 300:
            print(f"blob失败 {f}: {bd}"); sys.exit(1)
        items.append({"path": f, "mode": "100644", "type": "blob", "sha": bd["sha"]})
    print("blobs 完成")

    # 新 tree
    st, td = api("POST", f"https://api.github.com/repos/{REPO}/git/trees",
                 {"base_tree": base_tree, "tree": items})
    if st >= 300:
        print("tree失败:", td); sys.exit(1)

    # commit（保留远端历史为 parent）
    st, cm = api("POST", f"https://api.github.com/repos/{REPO}/git/commits",
                 {"message": MSG, "tree": td["sha"], "parents": [head]})
    if st >= 300:
        print("commit失败:", cm); sys.exit(1)

    # 更新 ref（force）
    st, rd = api("PATCH", f"https://api.github.com/repos/{REPO}/git/refs/heads/main",
                 {"sha": cm["sha"], "force": True})
    if st >= 300:
        print("ref失败:", rd); sys.exit(1)
    print(f"✅ 推送完成 commit={cm['sha'][:8]} parent={head[:8]} 文件={len(items)}")


if __name__ == "__main__":
    main()
