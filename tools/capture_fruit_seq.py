# -*- coding: utf-8 -*-
"""进对局后抓「下一个水果 / 投放序列」：
1. 自动进直播间 + 点开「合成大西瓜」面板
2. 等待你手动点开局（最长 8 分钟）
3. 一旦检测到 WatermelonGame 组件（对局开始）：
   - dump getDebugState() 全字段（找 nextFruit/currentFruit/queue/seed）
   - 抓 60s 内所有 WebSocket 帧 + 游戏相关 HTTP 请求
输出：fruit_seq.txt
"""
import os, re, time, json, binascii
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(ROOT, ".chrome-profile-diag")
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ROOM_URL = "https://www.douyu.com/4042402"
OUT = os.path.join(ROOT, "fruit_seq.txt")

KEY = re.compile(r"fruit|next|seq|seed|random|drop|watermelon|gamestart|startgame|level", re.I)
lines = []
def log(*a):
    s = " ".join(str(x) for x in a); lines.append(s); print(s, flush=True)

ENTER_JS = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getClientRects().length > 0;
  const norm = s => (s || '').replace(/\s+/g, '');
  const all = [];
  document.querySelectorAll('*').forEach(e => {
    const t = norm(e.textContent);
    if (!t.includes('合成大西瓜') || !vis(e)) return;
    const cs = getComputedStyle(e); const r = e.getBoundingClientRect();
    const area = r.width * r.height;
    if (area <= 0 || area > 600*600) return;
    const interactive = (e.tagName === 'BUTTON' || e.tagName === 'A'
      || e.getAttribute('role') === 'button' || cs.cursor === 'pointer');
    if (!interactive) return;
    all.push({x: r.x+r.width/2, y: r.y+r.height/2, area, tag: e.tagName,
              cls: (e.className||'').toString()});
  });
  const cards = all.filter(c => /tool|card|bar/i.test(c.cls || ''));
  const pick = cards.length ? cards : all;
  pick.sort((a,b)=>b.area-a.area);
  return pick[0] || null;
}
"""

FIND_GAME_JS = r"""
() => {
  const cc = window.cc || window.cclegacy || null;
  if (!cc || !cc.director || !cc.director.getScene) return {ok:false, reason:'no-cc'};
  const scene = cc.director.getScene();
  if (!scene) return {ok:false, reason:'no-scene'};
  const stack = [scene]; let found = null;
  while (stack.length) {
    const n = stack.pop();
    for (const c of (n.components || [])) {
      if (c && typeof c.dropCurrentFruitAt === 'function'
             && typeof c.getDebugState === 'function') { found = c; break; }
    }
    if (found) break;
    for (const ch of (n.children || [])) stack.push(ch);
  }
  if (!found) return {ok:false, reason:'no-component'};
  window.__WM_GAME = found;
  return {ok:true, name: (found.node && found.node.name)};
}
"""

# dump 游戏状态，重点找「下一个水果」
DUMP_STATE_JS = r"""
() => {
  const g = window.__WM_GAME;
  if (!g) return {err: 'no-game'};
  const out = {};
  // 组件自身的属性（可能有 nextFruit / queue / seed）
  try {
    const props = [];
    for (const k in g) {
      if (/fruit|next|seq|seed|random|queue|level|drop/i.test(k)) {
        let v = g[k];
        try { v = JSON.stringify(v); } catch(e) { v = String(v); }
        props.push(k + ' = ' + String(v).slice(0, 300));
      }
    }
    out['组件属性'] = props.slice(0, 60);
  } catch(e) { out['prop_err'] = String(e); }

  // getDebugState() 全量
  let st = null;
  try { st = g.getDebugState(); } catch(e) { out['state_err'] = String(e); }
  if (st && typeof st === 'object') {
    out['状态字段'] = Object.keys(st);
    const hit = [];
    for (const k of Object.keys(st)) {
      if (/fruit|next|seq|seed|random|queue|level|current|drop/i.test(k)) {
        hit.push(k + ' = ' + JSON.stringify(st[k]).slice(0, 400));
      }
    }
    out['状态-水果相关'] = hit;
    out['状态全文'] = JSON.stringify(st).slice(0, 3000);
  }
  return out;
}
"""

def fmt_payload(p):
    """WS 帧内容：文本直接截断，二进制给 hex 前缀"""
    try:
        if isinstance(p, (bytes, bytearray)):
            return f"<bin {len(p)}B> " + binascii.hexlify(p[:48]).decode()
        s = str(p)
        return s[:400]
    except Exception:
        return "<unprintable>"

def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, executable_path=CHROME, headless=False,
            viewport={"width":1500,"height":940},
            args=["--disable-blink-features=AutomationControlled","--no-first-run","--no-default-browser-check"],
            ignore_default_args=["--enable-automation"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        wsframes, httphits = [], []

        def on_ws(ws):
            log(f"[WS 连接] {ws.url[:160]}")
            ws.on("framereceived", lambda pl: wsframes.append(("RECV", ws.url[:60], fmt_payload(pl))))
            ws.on("framesent",     lambda pl: wsframes.append(("SENT", ws.url[:60], fmt_payload(pl))))
        page.on("websocket", on_ws)

        def on_resp(r):
            u = r.url
            if any(x in u.lower() for x in ('.js','.css','.png','.jpg','.woff','.svg','.gif','.webp')):
                return
            if KEY.search(u) or 'watermelon' in u.lower():
                body = ''
                try:
                    ct = (r.headers or {}).get('content-type','')
                    if 'json' in ct or 'text' in ct:
                        body = r.text()[:500]
                except Exception:
                    pass
                httphits.append((r.status, r.request.method, u[:170], body))
        page.on("response", on_resp)

        log("打开直播间")
        page.goto(ROOM_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(8)
        cand = page.evaluate(ENTER_JS)
        if cand:
            page.mouse.click(cand["x"], cand["y"])
            log(f"已点开「合成大西瓜」面板 @({cand['x']:.0f},{cand['y']:.0f})")
        log(">>> 请在浏览器里手动点「开始游戏/开启匹配」开一局，脚本会自动抓。")

        # 等待对局开始（最长 8 分钟）
        game_frame = None
        for i in range(240):
            for f in page.frames:
                try:
                    r = f.evaluate(FIND_GAME_JS)
                except Exception:
                    r = None
                if r and r.get('ok'):
                    game_frame = f
                    log(f"\n>>> 对局已开始！组件={r.get('name')}  frame={f.url[:100]}")
                    break
            if game_frame:
                break
            if i % 15 == 0 and i:
                log(f"  [等待开局 {(i*2)//60}分{(i*2)%60}秒] 还没检测到对局组件…")
            time.sleep(2)

        if not game_frame:
            log("\n超时：8 分钟内没检测到对局开始（需要匹配到对手）。")
            log("提示：这玩法是 PK 匹配，得有对手才能进对局。")
        else:
            # 1) dump 游戏状态
            log("\n=== 游戏内部状态（找下一个水果）===")
            for attempt in range(3):
                try:
                    st = game_frame.evaluate(DUMP_STATE_JS)
                except Exception as e:
                    st = {'err': str(e)}
                log(json.dumps(st, ensure_ascii=False, indent=1)[:6000])
                if attempt == 0:
                    log("\n（等待 5s 后再采一次，观察 next 是否变化）")
                time.sleep(5)

            # 2) 抓 60s 的 WS + HTTP
            log("\n=== 开始抓 60s 流量 ===")
            for i in range(6):
                time.sleep(10)
                log(f"  [t+{(i+1)*10}s] ws帧={len(wsframes)} http命中={len(httphits)}")

            log("\n=== WebSocket 帧（含 fruit/seq/next/seed 关键词优先）===")
            hit_frames = [f for f in wsframes if KEY.search(f[2])]
            log(f"(总帧 {len(wsframes)}，关键词命中 {len(hit_frames)})")
            for d, u, pl in (hit_frames or wsframes)[:60]:
                log(f"  [{d}] {u} :: {pl}")

            log("\n=== 游戏相关 HTTP ===")
            for s, m, u, b in httphits[:40]:
                log(f"  [{s}] {m} {u}")
                if b:
                    log(f"      {b[:300]}")

        log("\n--- 结束 ---")
        ctx.close()

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

if __name__ == "__main__":
    main()
