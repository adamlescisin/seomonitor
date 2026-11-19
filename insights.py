"""All analytics: turns raw GSC/GA4/crawl tables into Ahrefs-style reports."""
import re, unicodedata, datetime as dt
from urllib.parse import urlparse
from db import q

AI_RE = re.compile(r"(chatgpt|openai|perplexity|gemini\.google|bard\.google|copilot\.microsoft|claude\.ai|anthropic|you\.com|"
                   r"deepseek|kagi|phind|meta\.ai|grok|x\.ai|poe\.com|mistral|duckduckgo.*ai|brave.*leo)", re.I)
def is_ai(source): return bool(AI_RE.search(source or ""))
def ai_name(source):
    s = (source or "").lower()
    for k, n in (("chatgpt", "ChatGPT"), ("openai", "ChatGPT"), ("perplexity", "Perplexity"), ("gemini", "Gemini"),
                 ("bard", "Gemini"), ("copilot", "Copilot"), ("claude", "Claude"), ("anthropic", "Claude"),
                 ("deepseek", "DeepSeek"), ("you.com", "You.com"), ("kagi", "Kagi"), ("phind", "Phind"),
                 ("meta.ai", "Meta AI"), ("grok", "Grok"), ("x.ai", "Grok"), ("poe", "Poe"), ("mistral", "Mistral")):
        if k in s: return n
    return source

# rough organic CTR by position (industry averages); good enough to rank opportunities
CTR = {1: .28, 2: .15, 3: .10, 4: .07, 5: .05, 6: .04, 7: .03, 8: .025, 9: .02, 10: .018}
def exp_ctr(pos):
    p = int(round(pos))
    if p < 1: p = 1
    return CTR.get(p, 0.012 if p <= 15 else 0.006 if p <= 20 else 0.003)

def norm_kw(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()

def path_of(url):
    p = urlparse(url)
    return (p.path or "/") if p.netloc else url

def _maxdate(conn, table, site):
    r = conn.execute(f"SELECT MAX(date) d FROM {table} WHERE site=?", (site,)).fetchone()
    return r["d"]

def periods(conn, table, site, days):
    end = _maxdate(conn, table, site)
    if not end: return None
    e = dt.date.fromisoformat(end)
    cs, ps, pe = e - dt.timedelta(days=days - 1), e - dt.timedelta(days=2 * days - 1), e - dt.timedelta(days=days)
    return dict(cs=cs.isoformat(), ce=end, ps=ps.isoformat(), pe=pe.isoformat())

def delta(cur, prev):
    if not prev: return None
    return round((cur - prev) / prev * 100, 1)

# ---------------------------------------------------------------- overview
def overview(conn, site, days):
    out = {"days": days}
    pg = periods(conn, "gsc_daily", site, days)
    if pg:
        def agg(a, b):
            r = q(conn, "SELECT SUM(clicks) c, SUM(impressions) i, SUM(position*impressions)/NULLIF(SUM(impressions),0) p "
                        "FROM gsc_daily WHERE site=? AND date BETWEEN ? AND ?", (site, a, b))[0]
            return {k: (r[k] or 0) for k in ("c", "i", "p")}
        c, p = agg(pg["cs"], pg["ce"]), agg(pg["ps"], pg["pe"])
        out["gsc"] = {"range": [pg["cs"], pg["ce"]],
                      "clicks": c["c"], "clicks_d": delta(c["c"], p["c"]),
                      "impressions": c["i"], "impressions_d": delta(c["i"], p["i"]),
                      "ctr": round(c["c"] / c["i"] * 100, 2) if c["i"] else 0,
                      "position": round(c["p"], 1), "position_d": round(c["p"] - p["p"], 1) if p["p"] else None}
    pa = periods(conn, "ga_daily", site, days)
    if pa:
        rows = q(conn, "SELECT date,channel,source,sessions,users,engaged,conversions,revenue FROM ga_daily WHERE site=? AND date BETWEEN ? AND ?",
                 (site, pa["ps"], pa["ce"]))
        def tot(lo, hi, flt=lambda r: True):
            s = dict(sessions=0, users=0, engaged=0, conv=0, rev=0)
            for r in rows:
                if lo <= r["date"] <= hi and flt(r):
                    s["sessions"] += r["sessions"]; s["users"] += r["users"]; s["engaged"] += r["engaged"]
                    s["conv"] += r["conversions"]; s["rev"] += r["revenue"]
            return s
        al, ap = tot(pa["cs"], pa["ce"]), tot(pa["ps"], pa["pe"])
        ol = tot(pa["cs"], pa["ce"], lambda r: r["channel"] == "Organic Search")
        op = tot(pa["ps"], pa["pe"], lambda r: r["channel"] == "Organic Search")
        il = tot(pa["cs"], pa["ce"], lambda r: is_ai(r["source"]))
        ip = tot(pa["ps"], pa["pe"], lambda r: is_ai(r["source"]))
        out["ga"] = {"range": [pa["cs"], pa["ce"]],
                     "sessions": al["sessions"], "sessions_d": delta(al["sessions"], ap["sessions"]),
                     "users": al["users"], "users_d": delta(al["users"], ap["users"]),
                     "organic": ol["sessions"], "organic_d": delta(ol["sessions"], op["sessions"]),
                     "organic_share": round(ol["sessions"] / al["sessions"] * 100, 1) if al["sessions"] else 0,
                     "ai": il["sessions"], "ai_d": delta(il["sessions"], ip["sessions"]),
                     "revenue": round(al["rev"]), "revenue_d": delta(al["rev"], ap["rev"]),
                     "organic_revenue": round(ol["rev"]), "organic_revenue_d": delta(ol["rev"], op["rev"]),
                     "engagement_rate": round(al["engaged"] / al["sessions"] * 100, 1) if al["sessions"] else 0}
        ch = {}
        for r in rows:
            if pa["cs"] <= r["date"] <= pa["ce"]:
                ch[r["channel"]] = ch.get(r["channel"], 0) + r["sessions"]
        out["channels"] = sorted([{"channel": k, "sessions": v} for k, v in ch.items()], key=lambda x: -x["sessions"])
    cr = q(conn, "SELECT * FROM crawl_runs WHERE site=? ORDER BY id DESC LIMIT 1", (site,))
    if cr:
        n = q(conn, "SELECT severity, COUNT(*) n FROM crawl_issues WHERE run_id=? GROUP BY severity", (cr[0]["id"],))
        out["audit"] = {"score": cr[0]["score"], "pages": cr[0]["pages"], "finished": cr[0]["finished"],
                        "counts": {r["severity"]: r["n"] for r in n}}
    return out

def timeseries(conn, site, days):
    out = {}
    pg = periods(conn, "gsc_daily", site, days)
    if pg:
        out["gsc"] = q(conn, "SELECT date, clicks, impressions, position FROM gsc_daily WHERE site=? AND date BETWEEN ? AND ? ORDER BY date",
                       (site, pg["cs"], pg["ce"]))
    pa = periods(conn, "ga_daily", site, days)
    if pa:
        rows = q(conn, "SELECT date, channel, source, sessions, revenue FROM ga_daily WHERE site=? AND date BETWEEN ? AND ?",
                 (site, pa["cs"], pa["ce"]))
        d = {}
        for r in rows:
            x = d.setdefault(r["date"], {"date": r["date"], "sessions": 0, "organic": 0, "ai": 0, "revenue": 0})
            x["sessions"] += r["sessions"]; x["revenue"] += r["revenue"]
            if r["channel"] == "Organic Search": x["organic"] += r["sessions"]
            if is_ai(r["source"]): x["ai"] += r["sessions"]
        out["ga"] = [d[k] for k in sorted(d)]
    return out

# ---------------------------------------------------------------- search tables
def _qp(conn, site, a, b):
    return q(conn, "SELECT query, page, SUM(clicks) clicks, SUM(impressions) impr, "
                   "SUM(position*impressions)/NULLIF(SUM(impressions),0) pos FROM gsc_qp "
                   "WHERE site=? AND date BETWEEN ? AND ? GROUP BY query, page", (site, a, b))

def queries(conn, site, days, search="", limit=500):
    p = periods(conn, "gsc_qp", site, days)
    if not p: return []
    def agg(a, b):
        d = {}
        for r in _qp(conn, site, a, b):
            x = d.setdefault(r["query"], {"clicks": 0, "impr": 0, "pw": 0.0, "pages": {}})
            x["clicks"] += r["clicks"]; x["impr"] += r["impr"]; x["pw"] += (r["pos"] or 0) * r["impr"]
            x["pages"][r["page"]] = x["pages"].get(r["page"], 0) + r["impr"]
        return d
    cur, prev = agg(p["cs"], p["ce"]), agg(p["ps"], p["pe"])
    out = []
    for k, v in cur.items():
        if search and search.lower() not in k.lower(): continue
        pv = prev.get(k)
        pos = v["pw"] / v["impr"] if v["impr"] else 0
        ppos = (pv["pw"] / pv["impr"]) if pv and pv["impr"] else None
        out.append({"query": k, "clicks": v["clicks"], "impressions": v["impr"],
                    "ctr": round(v["clicks"] / v["impr"] * 100, 2) if v["impr"] else 0,
                    "position": round(pos, 1), "position_d": round(pos - ppos, 1) if ppos else None,
                    "clicks_d": v["clicks"] - (pv["clicks"] if pv else 0),
                    "page": max(v["pages"], key=v["pages"].get) if v["pages"] else ""})
    out.sort(key=lambda r: (-r["clicks"], -r["impressions"]))
    return out[:limit]

def pages(conn, site, days, search="", limit=300):
    p = periods(conn, "gsc_qp", site, days)
    if not p: return []
    def agg(a, b):
        d = {}
        for r in _qp(conn, site, a, b):
            x = d.setdefault(r["page"], {"clicks": 0, "impr": 0, "pw": 0.0, "q": 0})
            x["clicks"] += r["clicks"]; x["impr"] += r["impr"]; x["pw"] += (r["pos"] or 0) * r["impr"]; x["q"] += 1
        return d
    cur, prev = agg(p["cs"], p["ce"]), agg(p["ps"], p["pe"])
    ga = {}
    pa = periods(conn, "ga_landing", site, days)
    if pa:
        for r in q(conn, "SELECT landing, SUM(sessions) s, SUM(conversions) c, SUM(revenue) rev FROM ga_landing "
                         "WHERE site=? AND channel='Organic Search' AND date BETWEEN ? AND ? GROUP BY landing",
                   (site, pa["cs"], pa["ce"])):
            ga[r["landing"]] = r
    out = []
    for k, v in cur.items():
        if search and search.lower() not in k.lower(): continue
        pv = prev.get(k)
        g = ga.get(path_of(k), {})
        out.append({"page": k, "clicks": v["clicks"], "clicks_d": v["clicks"] - (pv["clicks"] if pv else 0),
                    "impressions": v["impr"], "position": round(v["pw"] / v["impr"], 1) if v["impr"] else 0,
                    "queries": v["q"], "organic_sessions": g.get("s", 0), "organic_revenue": round(g.get("rev", 0) or 0)})
    out.sort(key=lambda r: -r["clicks"])
    return out[:limit]

# ---------------------------------------------------------------- opportunities
def opportunities(conn, site, days, min_impr):
    p = periods(conn, "gsc_qp", site, days)
    if not p: return {}
    cur_rows, prev_rows = _qp(conn, site, p["cs"], p["ce"]), _qp(conn, site, p["ps"], p["pe"])
    by_q = {}
    for r in cur_rows:
        x = by_q.setdefault(r["query"], {"clicks": 0, "impr": 0, "pw": 0.0, "pages": []})
        x["clicks"] += r["clicks"]; x["impr"] += r["impr"]; x["pw"] += (r["pos"] or 0) * r["impr"]
        x["pages"].append(r)
    for x in by_q.values():
        x["pos"] = x["pw"] / x["impr"] if x["impr"] else 0
        x["top"] = max(x["pages"], key=lambda r: r["impr"])["page"]
    striking, lowctr, cannibal = [], [], []
    for k, x in by_q.items():
        if x["impr"] < min_impr: continue
        ctr = x["clicks"] / x["impr"]
        if 4 <= x["pos"] <= 20:
            gain = max(0, x["impr"] * exp_ctr(3) - x["clicks"])
            if gain >= 1:
                striking.append({"query": k, "impressions": x["impr"], "clicks": x["clicks"], "position": round(x["pos"], 1),
                                 "potential_clicks": round(gain), "page": x["top"]})
        if x["pos"] <= 5 and ctr < 0.6 * exp_ctr(x["pos"]):
            gain = x["impr"] * exp_ctr(x["pos"]) - x["clicks"]
            if gain >= 1:
                lowctr.append({"query": k, "impressions": x["impr"], "clicks": x["clicks"], "ctr": round(ctr * 100, 2),
                               "expected_ctr": round(exp_ctr(x["pos"]) * 100, 1), "position": round(x["pos"], 1),
                               "potential_clicks": round(gain), "page": x["top"]})
        big = [r for r in x["pages"] if r["impr"] >= 0.15 * x["impr"] and r["impr"] >= max(10, min_impr * 0.3)]
        if len(big) >= 2:
            cannibal.append({"query": k, "impressions": x["impr"], "clicks": x["clicks"],
                             "pages": [{"page": r["page"], "impressions": r["impr"], "clicks": r["clicks"], "position": round(r["pos"], 1)}
                                       for r in sorted(big, key=lambda r: -r["impr"])[:4]]})
    striking.sort(key=lambda r: -r["potential_clicks"]); lowctr.sort(key=lambda r: -r["potential_clicks"])
    cannibal.sort(key=lambda r: -r["impressions"])
    # declines / gains by page and query
    def sums(rows, key):
        d = {}
        for r in rows: d[r[key]] = d.get(r[key], 0) + r["clicks"]
        return d
    cp, pp = sums(cur_rows, "page"), sums(prev_rows, "page")
    cq, pq = sums(cur_rows, "query"), sums(prev_rows, "query")
    declining = sorted([{"page": k, "before": pp[k], "now": cp.get(k, 0), "lost": pp[k] - cp.get(k, 0)}
                        for k in pp if pp[k] >= 10 and cp.get(k, 0) <= 0.7 * pp[k]], key=lambda r: -r["lost"])[:30]
    lost_q = sorted([{"query": k, "before": pq[k], "now": cq.get(k, 0), "lost": pq[k] - cq.get(k, 0)}
                     for k in pq if pq[k] >= 5 and cq.get(k, 0) <= 0.5 * pq[k]], key=lambda r: -r["lost"])[:30]
    rising = sorted([{"query": k, "before": pq.get(k, 0), "now": v, "gain": v - pq.get(k, 0)}
                     for k, v in cq.items() if v - pq.get(k, 0) >= 5], key=lambda r: -r["gain"])[:30]
    # new keywords you started ranking for (impressions in this period, none in previous)
    prev_set = {r["query"] for r in prev_rows}
    newk = {}
    for r in cur_rows:
        if r["query"] not in prev_set:
            x = newk.setdefault(r["query"], {"query": r["query"], "impressions": 0, "clicks": 0, "pw": 0.0})
            x["impressions"] += r["impr"]; x["clicks"] += r["clicks"]; x["pw"] += (r["pos"] or 0) * r["impr"]
    new_queries = sorted([{"query": v["query"], "impressions": v["impressions"], "clicks": v["clicks"],
                           "position": round(v["pw"] / v["impressions"], 1)} for v in newk.values() if v["impressions"] >= max(5, min_impr * 0.3)],
                         key=lambda r: -r["impressions"])[:30]
    # no-click pages: lots of impressions, zero clicks
    pgimp = {}
    for r in cur_rows:
        x = pgimp.setdefault(r["page"], [0, 0]); x[0] += r["impr"]; x[1] += r["clicks"]
    zero = sorted([{"page": k, "impressions": v[0]} for k, v in pgimp.items() if v[1] == 0 and v[0] >= min_impr * 2],
                  key=lambda r: -r["impressions"])[:20]
    return {"striking_distance": striking[:60], "low_ctr": lowctr[:40], "cannibalisation": cannibal[:30],
            "declining_pages": declining, "lost_queries": lost_q, "rising_queries": rising,
            "new_queries": new_queries, "zero_click_pages": zero,
            "summary": {"striking_potential": sum(r["potential_clicks"] for r in striking),
                        "ctr_potential": sum(r["potential_clicks"] for r in lowctr)}}

def breakdown(conn, site, days, dim):
    p = periods(conn, "gsc_dim", site, days)
    if not p: return []
    return q(conn, "SELECT value, SUM(clicks) clicks, SUM(impressions) impressions, "
                   "SUM(position*impressions)/NULLIF(SUM(impressions),0) position FROM gsc_dim "
                   "WHERE site=? AND dim=? AND date BETWEEN ? AND ? GROUP BY value ORDER BY clicks DESC LIMIT 12",
             (site, dim, p["cs"], p["ce"]))

# ---------------------------------------------------------------- GEO / AI
def ai_report(conn, site, days):
    pa = periods(conn, "ga_daily", site, days)
    out = {"sources": [], "series": [], "landing": [], "checks": q(conn, "SELECT key,status,detail FROM geo_checks WHERE site=?", (site,))}
    if not pa: return out
    rows = [r for r in q(conn, "SELECT date,source,sessions,engaged,conversions,revenue FROM ga_daily WHERE site=? AND date BETWEEN ? AND ?",
                         (site, pa["ps"], pa["ce"])) if is_ai(r["source"])]
    cur, prev, series = {}, {}, {}
    for r in rows:
        n = ai_name(r["source"])
        if r["date"] >= pa["cs"]:
            x = cur.setdefault(n, {"source": n, "sessions": 0, "engaged": 0, "conversions": 0, "revenue": 0})
            x["sessions"] += r["sessions"]; x["engaged"] += r["engaged"]; x["conversions"] += r["conversions"]; x["revenue"] += r["revenue"]
            s = series.setdefault(r["date"], {"date": r["date"]}); s[n] = s.get(n, 0) + r["sessions"]
        elif r["date"] <= pa["pe"]:
            prev[n] = prev.get(n, 0) + r["sessions"]
    for n, x in cur.items():
        x["delta"] = delta(x["sessions"], prev.get(n, 0)); x["revenue"] = round(x["revenue"])
    out["sources"] = sorted(cur.values(), key=lambda x: -x["sessions"])
    out["series"] = [series[k] for k in sorted(series)]
    land = q(conn, "SELECT landing, source, SUM(sessions) s, SUM(conversions) c FROM ga_landing WHERE site=? AND date BETWEEN ? AND ? GROUP BY landing, source",
             (site, pa["cs"], pa["ce"]))
    agg = {}
    for r in land:
        if is_ai(r["source"]):
            agg[r["landing"]] = agg.get(r["landing"], 0) + r["s"]
    out["landing"] = sorted([{"page": k, "sessions": v} for k, v in agg.items()], key=lambda x: -x["sessions"])[:20]
    return out

# ---------------------------------------------------------------- crawl / ideas
def audit(conn, site, severity=None):
    cr = q(conn, "SELECT * FROM crawl_runs WHERE site=? ORDER BY id DESC LIMIT 2", (site,))
    if not cr: return {"run": None}
    rid = cr[0]["id"]
    types = q(conn, "SELECT type, severity, COUNT(*) n FROM crawl_issues WHERE run_id=? GROUP BY type, severity ORDER BY "
                    "CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, n DESC", (rid,))
    prev = {}
    if len(cr) > 1:
        for r in q(conn, "SELECT type, COUNT(*) n FROM crawl_issues WHERE run_id=? GROUP BY type", (cr[1]["id"],)):
            prev[r["type"]] = r["n"]
    for t in types: t["delta"] = t["n"] - prev.get(t["type"], 0) if prev else None
    return {"run": cr[0], "types": types, "prev_score": cr[1]["score"] if len(cr) > 1 else None}

def audit_detail(conn, site, issue_type):
    cr = q(conn, "SELECT id FROM crawl_runs WHERE site=? ORDER BY id DESC LIMIT 1", (site,))
    if not cr: return []
    return q(conn, "SELECT url, severity, detail FROM crawl_issues WHERE run_id=? AND type=? LIMIT 300", (cr[0]["id"], issue_type))

def crawl_pages(conn, site, limit=500):
    cr = q(conn, "SELECT id FROM crawl_runs WHERE site=? ORDER BY id DESC LIMIT 1", (site,))
    if not cr: return []
    return q(conn, "SELECT url,status,ms,title,words,inlinks,in_sitemap,noindex,depth,schema_types FROM crawl_pages WHERE run_id=? ORDER BY inlinks DESC LIMIT ?", (cr[0]["id"], limit))

def ideas(conn, site):
    known = {norm_kw(r["query"]) for r in q(conn, "SELECT DISTINCT query FROM gsc_qp WHERE site=?", (site,))}
    out = []
    for r in q(conn, "SELECT idea, seed, kind FROM keyword_ideas WHERE site=?", (site,)):
        n = norm_kw(r["idea"])
        r["ranking"] = n in known
        out.append(r)
    order = {"commercial": 0, "question": 1, "topic": 2}
    out.sort(key=lambda r: (r["ranking"], order.get(r["kind"], 3), r["idea"]))
    return out
