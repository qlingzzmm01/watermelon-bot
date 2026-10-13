# -*- coding: utf-8 -*-
"""构建纯净发布包：只含 exe + _internal 依赖。

⚠️ 安全红线：绝不打包 .chrome-profile/（含斗鱼登录 Cookie）、
.room_url.txt（个人直播间）、telemetry_id.txt（本机设备 ID）。
用法: python tools/make_release_zip.py [版本号，默认 1.2.1]
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "合成大西瓜自动化")
VER = sys.argv[1] if len(sys.argv) > 1 else "1.2.1"
OUT = os.path.join(ROOT, f"合成大西瓜自动化_发布_v{VER}.zip")

# 必须排除（安全）
EXCLUDE_TOP = {".chrome-profile", ".edge-profile", ".brave-profile",
               ".room_url.txt", "telemetry_id.txt"}
# 始终排除的杂项
EXCLUDE_ANY = {"__pycache__", ".DS_Store", "crash.log"}


def main():
    if not os.path.isdir(SRC):
        print(f"源目录不存在: {SRC}")
        sys.exit(1)
    if os.path.exists(OUT):
        os.remove(OUT)

    n_files = 0
    total = 0
    skipped = []
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(SRC):
            rel = os.path.relpath(dirpath, SRC)
            parts = [] if rel == "." else rel.split(os.sep)
            # 顶层排除
            if parts and parts[0] in EXCLUDE_TOP:
                skipped.append("/".join(parts))
                dirnames[:] = []
                continue
            # 剪枝
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_ANY
                           and d not in EXCLUDE_TOP]
            for fn in filenames:
                if fn in EXCLUDE_ANY:
                    continue
                if not parts and fn in EXCLUDE_TOP:
                    skipped.append(fn)
                    continue
                full = os.path.join(dirpath, fn)
                if not os.path.isfile(full):
                    continue
                arc = os.path.join("合成大西瓜自动化", rel, fn) if rel != "." \
                    else os.path.join("合成大西瓜自动化", fn)
                z.write(full, arc)
                n_files += 1
                total += os.path.getsize(full)

    size_mb = os.path.getsize(OUT) / 1048576
    print(f"✅ 打包完成: {os.path.basename(OUT)}")
    print(f"   文件数: {n_files}  原始大小: {total/1048576:.1f}MB  压缩后: {size_mb:.1f}MB")
    print(f"   已排除: {sorted(set(skipped))}")

    # 安全自检：确认包内无 Cookie/profile/直播间
    print("\n=== 安全自检 ===")
    bad = []
    with zipfile.ZipFile(OUT) as z:
        for n in z.namelist():
            low = n.lower()
            if any(k in low for k in (".chrome-profile", "cookies", "login data",
                                      "web data", "history", "local state",
                                      ".room_url", "telemetry_id")):
                bad.append(n)
    if bad:
        print(f"❌ 包内发现敏感文件 {len(bad)} 个：")
        for b in bad[:10]:
            print("   -", b)
        sys.exit(1)
    print("✅ 无 profile / Cookie / 登录数据 / 个人直播间")


if __name__ == "__main__":
    main()
