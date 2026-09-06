# -*- coding: utf-8 -*-
"""上传 zip 到 GitHub Release asset（ASCII 名）。"""
import json
import sys
import urllib.request

token, release_id, zippath, asset = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
with open(zippath, "rb") as f:
    data = f.read()
url = (f"https://uploads.github.com/repos/qlingzzmm01/watermelon-bot/"
       f"releases/{release_id}/assets?name={asset}&label=Windows")
req = urllib.request.Request(url, data=data, method="POST",
                             headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/vnd.github+json",
                                      "Content-Type": "application/zip"})
try:
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
        print(f"OK asset_id={d.get('id')} size={d.get('size')}")
except urllib.error.HTTPError as e:
    print(f"HTTP {e.code}: {e.read().decode()[:200]}")
