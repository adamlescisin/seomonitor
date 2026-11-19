"""Synthetic but realistic data so you can explore the dashboard before connecting Google."""
import random, math, datetime as dt, json

QUERIES = [  # (query, base impressions/day, base position, page)
 ("detske tricko", 220, 7.5, "/kategorie/tricka/"), ("kojenecke body", 180, 5.2, "/kategorie/body/"),
 ("detske teplaky", 150, 9.4, "/kategorie/teplaky/"), ("zimni bunda pro deti", 260, 12.8, "/kategorie/bundy/"),
 ("mimimami", 90, 1.3, "/"), ("detske leginy", 120, 6.1, "/kategorie/leginy/"), ("detske saty", 200, 11.0, "/kategorie/saty/"),
 ("bavlnene body kojenecke", 70, 4.4, "/kategorie/body/"), ("obleceni pro miminka", 160, 14.2, "/kategorie/miminka/"),
 ("detske plavky", 110, 8.8, "/kategorie/plavky/"), ("kojenecka soupravicka", 60, 3.1, "/produkt/souprava-medvidek/"),
 ("detska mikina", 140, 6.9, "/kategorie/mikiny/"), ("detske kalhoty", 190, 10.5, "/kategorie/kalhoty/"),
 ("velikosti detskeho obleceni", 130, 5.5, "/blog/velikosti-detskeho-obleceni/"), ("jak vybrat velikost body", 40, 4.8, "/blog/velikosti-detskeho-obleceni/"),
 ("detske obleceni eshop", 100, 15.5, "/"), ("levne detske obleceni", 170, 18.0, "/kategorie/akce/"), ("detske pyzamo", 80, 7.2, "/kategorie/pyzama/"),
 ("detske cepice", 90, 9.1, "/kategorie/cepice/"), ("detske ponozky", 75, 11.7, "/kategorie/ponozky/"),
 ("obleceni pro holky", 150, 16.0, "/kategorie/holky/"), ("obleceni pro kluky", 150, 13.0, "/kategorie/kluci/"),
 ("co obleci miminko na zimu", 35, 6.3, "/blog/jak-obleci-miminko-na-zimu/"), ("detske tricko s potiskem", 95, 5.0, "/kategorie/tricka/"),
 ("detske body s dlouhym rukavem", 85, 8.2, "/kategorie/body/"), ("detske body s dlouhym rukavem", 30, 12.0, "/kategorie/miminka/"),
 ("zimni overal miminko", 70, 3.9, "/kategorie/overaly/"), ("detske mikiny na zip", 55, 7.7, "/kategorie/mikiny/"),
 ("elka fashion", 45, 2.0, "/"), ("organicke bavlna deti", 25, 9.5, "/blog/organicka-bavlna/"),
]
NEW = {"zimni overal miminko", "organicke bavlna deti", "detske mikiny na zip"}
SOURCES = [("Organic Search", "google", "organic", 1.0), ("Organic Search", "seznam", "organic", 0.12), ("Organic Search", "bing", "organic", 0.04),
           ("Direct", "(direct)", "(none)", 0.45), ("Paid Search", "google", "cpc", 0.25), ("Social", "facebook", "social", 0.18),
           ("Social", "instagram", "social", 0.14), ("Referral", "chatgpt.com", "referral", 0.0), ("Referral", "perplexity.ai", "referral", 0.0),
           ("Referral", "gemini.google.com", "referral", 0.0), ("Referral", "copilot.microsoft.com", "referral", 0.0), ("Email", "newsletter", "email", 0.10)]

def ensure(cfg, conn):
    if conn.execute("SELECT COUNT(*) FROM gsc_daily").fetchone()[0] > 0: return
    random.seed(7)
    today = dt.date.today()
    days = [today - dt.timedelta(days=i) for i in range(420, 1, -1)]
    for si, site in enumerate(cfg["sites"]):
        sid = site["id"]; mult = 1.0 if si == 0 else 0.55
        qp_rows, daily = [], {}
        for qi, (qry, base, pos, page) in enumerate(QUERIES):
            trend = random.uniform(-0.25, 0.7)
            for di, d in enumerate(days):
                season = 1 + 0.25 * math.sin((d.timetuple().tm_yday / 365) * 2 * math.pi + qi) + (0.3 if d.month in (10, 11, 12) and "zim" in qry else 0)
                impr = max(0, int(random.gauss(base * mult * season * (1 + trend * di / len(days)) * (0.7 if d.weekday() > 4 else 1), base * 0.15)))
                if impr == 0: continue
                p = max(1.0, random.gauss(pos - 0.8 * trend * di / len(days), 0.8))
                ctr = max(0.0, random.gauss({1: .28, 2: .15, 3: .1}.get(round(p), 0.07 if p < 5 else 0.03 if p < 10 else 0.01), 0.01))
                if qry == "detske tricko" and di > len(days) - 20: p += 5; ctr *= .5; impr = int(impr * .6)   # example decline
                if qry == "kojenecka soupravicka": ctr = 0.012                                          # example low-CTR snippet
                if qry in NEW and di < len(days) - 16: continue                                          # example new keywords
                clicks = int(impr * ctr)
                qp_rows.append((sid, d.isoformat(), qry, f"https://{site['domain']}{page}", clicks, impr, round(p, 2)))
                x = daily.setdefault(d.isoformat(), [0, 0, 0.0]); x[0] += clicks; x[1] += impr; x[2] += p * impr
        conn.executemany("INSERT OR REPLACE INTO gsc_qp VALUES (?,?,?,?,?,?,?)", qp_rows)
        for dd, (c, i, pw) in daily.items():
            i2 = int(i * 1.35); c2 = int(c * 1.12)    # anonymised queries not in per-query rows
            conn.execute("INSERT OR REPLACE INTO gsc_daily VALUES (?,?,?,?,?,?)", (sid, dd, c2, i2, c2 / i2, pw / i))
            for dev, share in (("MOBILE", .68), ("DESKTOP", .29), ("TABLET", .03)):
                conn.execute("INSERT OR REPLACE INTO gsc_dim VALUES (?,?,?,?,?,?,?)", (sid, dd, "device", dev, int(c2 * share), int(i2 * share), pw / i))
            for ctry, share in (("cze", .86), ("svk", .09), ("pol", .02), ("deu", .015), ("usa", .015)):
                conn.execute("INSERT OR REPLACE INTO gsc_dim VALUES (?,?,?,?,?,?,?)", (sid, dd, "country", ctry, int(c2 * share), int(i2 * share), pw / i))
        # GA4
        pages = sorted({q[3] for q in QUERIES})
        for di, d in enumerate(days):
            dd = d.isoformat(); g = daily.get(dd, [0, 0, 0])[0]
            growth = di / len(days)
            for ch, src, med, w in SOURCES:
                if src in ("chatgpt.com", "perplexity.ai", "gemini.google.com", "copilot.microsoft.com"):
                    start = {"chatgpt.com": 0.35, "perplexity.ai": 0.55, "gemini.google.com": 0.75, "copilot.microsoft.com": 0.7}[src]
                    sess = max(0, int(random.gauss(max(0, growth - start) * {"chatgpt.com": 55, "perplexity.ai": 20, "gemini.google.com": 14, "copilot.microsoft.com": 6}[src] * mult, 1.5)))
                else:
                    sess = max(0, int(random.gauss(g * 1.1 * w * (0.7 if ch == "Social" else 1) + (25 * w * mult), 3 + g * .03)))
                if sess == 0: continue
                eng = int(sess * random.uniform(.5, .75)); conv = round(sess * random.uniform(.008, .03), 2)
                rev = round(conv * random.uniform(550, 900), 2)
                conn.execute("INSERT OR REPLACE INTO ga_daily VALUES (?,?,?,?,?,?,?,?,?,?)", (sid, dd, ch, src, med, sess, int(sess * .85), eng, conv, rev))
                for pg in random.sample(pages, min(3, len(pages))):
                    s2 = max(1, sess // 3)
                    conn.execute("INSERT OR REPLACE INTO ga_landing VALUES (?,?,?,?,?,?,?,?,?)", (sid, dd, pg, ch, src, s2, int(s2 * .6), conv / 3, rev / 3))
        # crawl
        _demo_crawl(conn, site, pages)
        ideas = ["detske tricko bio bavlna", "jak vybrat detske tricko", "detske tricko dlouhy rukav", "kojenecke body 62", "co koupit k narozeni miminka",
                 "kolik body potrebuje miminko", "detske obleceni slevy", "koupit detske obleceni online", "detska mikina s kapuci", "nejlepsi znacky detskeho obleceni",
                 "jak prat vlnene obleceni pro deti", "detske obleceni podle veku", "ponozky pro miminka", "jaky overal pro miminko na zimu"]
        for idea in ideas:
            kind = "question" if idea.split()[0] in ("jak", "kolik", "co", "jaky") else "commercial" if any(w in idea for w in ("koupit", "slevy", "nejlepsi")) else "topic"
            conn.execute("INSERT OR IGNORE INTO keyword_ideas VALUES (?,?,?,?,datetime('now'))", (sid, idea, "detske tricko", kind))
    conn.commit()

def _demo_crawl(conn, site, pages):
    now = dt.datetime.now()
    types = [("Missing meta description", "warning", 12), ("Title too long", "warning", 9), ("Images without alt text", "notice", 22),
             ("Broken internal link", "error", 4), ("Redirect chain", "warning", 3), ("Duplicate title", "warning", 6),
             ("Thin content", "notice", 8), ("Orphan page (no internal links)", "warning", 5), ("Slow response", "warning", 3),
             ("Missing canonical", "notice", 7), ("Indexable page not in sitemap", "notice", 4), ("Missing H1", "warning", 2)]
    for k, finished in ((1, now - dt.timedelta(days=7)), (2, now)):
        cur = conn.execute("INSERT INTO crawl_runs (site,started,finished,pages,score,meta) VALUES (?,?,?,?,?,?)",
                           (site["id"], finished.isoformat(timespec="seconds"), finished.isoformat(timespec="seconds"), 180 if k == 2 else 176, 0, "{}"))
        rid = cur.lastrowid; total = 0
        urls = [f"https://{site['domain']}{p}" for p in pages] + [f"https://{site['domain']}/produkt/vyrobek-{i}/" for i in range(1, 60)]
        for u in urls:
            conn.execute("INSERT INTO crawl_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, u, 200, random.randint(200, 1600), "Title", "Desc", 1, u, 0, random.randint(80, 900), 0, random.randint(0, 40), 1,
                          random.choice(["Product,BreadcrumbList", "Organization", "", "Article"]), random.randint(0, 4)))
        for t, sev, n in types:
            n2 = max(0, n + (random.randint(-2, 2) if k == 1 else 0)); total += n2 * {"error": 3, "warning": 1, "notice": .2}[sev]
            for i in range(n2):
                conn.execute("INSERT INTO crawl_issues VALUES (?,?,?,?,?)", (rid, random.choice(urls), t, sev, ""))
        conn.execute("UPDATE crawl_runs SET score=? WHERE id=?", (round(100 / (1 + total / 180), 1), rid))
    rows = [("bot:GPTBot", "allowed"), ("bot:OAI-SearchBot", "allowed"), ("bot:ClaudeBot", "allowed"), ("bot:PerplexityBot", "allowed"),
            ("bot:Google-Extended", "blocked"), ("bot:CCBot", "blocked"), ("bot:Bytespider", "blocked"), ("llms.txt", "missing"),
            ("schema:Product", "64%"), ("schema:FAQPage", "2%"), ("schema:Organization", "100%"), ("schema:BreadcrumbList", "88%"), ("schema:Article", "9%")]
    conn.execute("DELETE FROM geo_checks WHERE site=?", (site["id"],))
    conn.executemany("INSERT INTO geo_checks VALUES (?,?,?,?,?)", [(site["id"], now.isoformat(), k, v, "") for k, v in rows])
