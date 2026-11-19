"""Keyword ideas via Google Autocomplete (free, no volume data) seeded from your own Search Console queries."""
import time, string, datetime as dt, requests
from db import q, log

QUESTION = {"cs": ["jak", "co", "proc", "kde", "kolik", "jaky", "kdy", "je"], "en": ["how", "what", "why", "where", "which", "best", "can"]}
COMMERCIAL = {"cs": ["koupit", "cena", "levne", "akce", "eshop", "e-shop", "recenze", "nejlepsi", "nejlepší", "sleva", "obchod", "srovnani", "test"],
              "en": ["buy", "price", "cheap", "deal", "shop", "review", "best", "discount", "vs", "compare"]}

def kind_of(idea, lang):
    w = idea.lower().split()
    if w and w[0] in QUESTION.get(lang, QUESTION["en"]): return "question"
    if any(c in idea.lower() for c in COMMERCIAL.get(lang, COMMERCIAL["en"])): return "commercial"
    return "topic"

def suggest(term, lang, country):
    r = requests.get("https://suggestqueries.google.com/complete/search",
                     params={"client": "firefox", "q": term, "hl": lang, "gl": country}, timeout=10,
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json()[1]

def fetch_ideas(cfg, conn, site, max_seeds=12):
    lang, country = site.get("language", "en"), site.get("country", "us")
    seeds = list(site.get("seed_keywords", []))
    top = q(conn, "SELECT query, SUM(impressions) i FROM gsc_qp WHERE site=? AND date >= date('now','-90 day') "
                  "GROUP BY query ORDER BY i DESC LIMIT 40", (site["id"],))
    for r in top:
        if len(r["query"].split()) <= 4 and r["query"] not in seeds: seeds.append(r["query"])
    seeds = seeds[:max_seeds]
    now, n = dt.datetime.now().isoformat(timespec="seconds"), 0
    for seed in seeds:
        terms = [seed] + [f"{seed} {c}" for c in string.ascii_lowercase] + [f"{w} {seed}" for w in QUESTION.get(lang, QUESTION["en"])]
        for t in terms:
            try:
                for idea in suggest(t, lang, country):
                    if idea.strip().lower() == seed.lower(): continue
                    conn.execute("INSERT OR IGNORE INTO keyword_ideas VALUES (?,?,?,?,?)", (site["id"], idea.strip(), seed, kind_of(idea, lang), now))
                    n += 1
            except Exception:
                time.sleep(1)
            time.sleep(0.25)
        conn.commit()
    log(conn, site["id"], "ideas", True, f"{len(seeds)} seeds, {n} suggestions")
    return n
