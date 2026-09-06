import json, os, glob, collections, datetime

base = os.path.expanduser("~/.workbuddy/projects/c-Users-Administrator-Desktop-大西瓜")

def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "usage" and isinstance(v, dict):
                yield path + "/" + k, v
            yield from walk(v, path + "/" + k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, path + "[]")

def ts(o):
    t = o.get("timestamp") or o.get("createdAt") or o.get("time")
    if isinstance(t, (int, float)):
        return datetime.datetime.fromtimestamp(t/1000 if t > 1e11 else t)
    if isinstance(t, str):
        try:
            return datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
        except Exception:
            pass
    return None

grand = collections.Counter()
rows = []
for f in sorted(glob.glob(os.path.join(base, "*.jsonl")), key=os.path.getsize, reverse=True):
    req = inp = outp = cr = cw = 0
    first = last = None
    firstuser = ""
    byday = collections.Counter()
    for line in open(f, encoding="utf-8", errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        t = ts(o)
        if t:
            first = first or t
            last = t
        if o.get("type") == "message":
            m = o.get("message") or {}
            if m.get("role") == "user" and not firstuser:
                c = m.get("content")
                if isinstance(c, str):
                    firstuser = c
                elif isinstance(c, list):
                    for b in c:
                        if isinstance(b, dict) and b.get("type") == "text":
                            firstuser = b["text"]
                            break
        for p, u in walk(o):
            if p.endswith("/providerData/usage"):
                req += u.get("requests", 0)
                inp += u.get("inputTokens", 0)
                outp += u.get("outputTokens", 0)
            elif p.endswith("/message/usage"):
                cr += u.get("cache_read_input_tokens", 0)
                cw += u.get("cache_creation_input_tokens", 0)
                if t:
                    byday[t.strftime("%Y-%m-%d")] += u.get("input_tokens", 0)
    rows.append((os.path.basename(f)[:8], first, last, req, inp, outp, cr, cw, dict(byday), firstuser[:80]))
    grand["req"] += req; grand["inp"] += inp; grand["out"] += outp
    grand["cr"] += cr; grand["cw"] += cw

def fmt(n): return f"{n:,}"
print(f"{'session':10}{'start':20}{'end':20}{'req':>6}{'input':>14}{'cache_read':>14}{'new':>11}{'output':>10}")
for s, a, b, req, inp, outp, cr, cw, bd, fu in rows:
    print(f"{s:10}{str(a)[:19]:20}{str(b)[:19]:20}{req:>6}{inp:>14,}{cr:>14,}{inp-cr:>11,}{outp:>10,}")
    print(f"   首条用户消息: {fu}")
    if len(bd) > 1:
        print("   按日 input:", {k: fmt(v) for k, v in sorted(bd.items())})
print("-" * 95)
print(f"{'合计':10}{'':40}{grand['req']:>6}{grand['inp']:>14,}{grand['cr']:>14,}{grand['inp']-grand['cr']:>11,}{grand['out']:>10,}")
