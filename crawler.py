"""Small polite site crawler producing Ahrefs-style 'Site Audit' issues."""
import re, time, json, datetime as dt
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, urldefrag
import requests
from bs4 import BeautifulSoup
from db import log

UA = "SEOCompassBot/1.0 (+own-site audit)"
SKIP_EXT = re.compile(r"\.(jpe?g|png|gif|webp|svg|pdf|zip|css|js|ico|woff2?|mp4|mp3|xml)(\?|$)", re.I)
# WooCommerce / WordPress noise that shouldn't be audited as content
SKIP_PATH = re.compile(r"(/wp-admin|/wp-json|/cart|/checkout|/my-account|/kosik|/pokladna|/muj-ucet|\?add-to-cart|\?orderby|[?&]filter_|[?&]min_price|[?&]max_price|/feed/?$|/xmlrpc)", re.I)

def norm(url):
    url, _ = urldefrag(url)
    p = urlparse(url)
    path = p.path or "/"
    return p._replace(path=path, netloc=p.netloc.lower()).geturl()

def fetch_sitemap_urls(sess, sm_url, seen=None, depth=0):
    seen = seen if seen is not None else set()
    urls = []
    if sm_url in seen or depth > 3:
        return urls
    seen.add(sm_url)
    try:
        r = sess.get(sm_url, timeout=20)
        if r.status_code != 200:
            return urls
        soup = BeautifulSoup(r.text, "html.parser")
        for sm in soup.find_all("sitemap"):
            loc = sm.find("loc")
            if loc:
                urls += fetch_sitemap_urls(sess, loc.text.strip(), seen, depth + 1)
        for u in soup.find_all("url"):
            loc = u.find("loc")
            if loc:
                urls.append(norm(loc.text.strip()))
    except Exception:
        pass
    return urls

def analyse(url, resp, ms, depth, host):
    d = {"url": url, "status": resp.status_code, "ms": ms, "title": "", "meta_desc": "", "h1_count": 0,
         "canonical": "", "noindex": 0, "words": 0, "imgs_no_alt": 0, "schema_types": "", "depth": depth,
         "links": [], "headers_robots": resp.headers.get("X-Robots-Tag", "")}
    ctype = resp.headers.get("Content-Type", "")
    if resp.status_code != 200 or "html" not in ctype:
        return d
    d["is_html"] = True
    soup = BeautifulSoup(resp.text, "html.parser")
    t = soup.find("title")
    d["title"] = (t.get_text(strip=True) if t else "")
    m = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    d["meta_desc"] = (m.get("content", "").strip() if m else "")
    d["h1_count"] = len(soup.find_all("h1"))
    c = soup.find("link", rel="canonical")
    d["canonical"] = c.get("href", "").strip() if c else ""
    rb = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
    d["noindex"] = int(bool((rb and "noindex" in rb.get("content", "").lower()) or "noindex" in d["headers_robots"].lower()))
    d["imgs_no_alt"] = sum(1 for i in soup.find_all("img") if not (i.get("alt") or "").strip())
    d["og"] = bool(soup.find("meta", property="og:title"))
    d["viewport"] = bool(soup.find("meta", attrs={"name": "viewport"}))
    types = set()
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            j = json.loads(s.string or "")
            stack = [j]
            while stack:
                x = stack.pop()
                if isinstance(x, list):
                    stack += x
                elif isinstance(x, dict):
                    ty = x.get("@type")
                    if ty:
                        types.update(ty if isinstance(ty, list) else [ty])
                    stack += [v for v in x.values() if isinstance(v, (dict, list))]
        except Exception:
            pass
    d["schema_types"] = ",".join(sorted(types))
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    d["words"] = len(soup.get_text(" ", strip=True).split())
    soup2 = BeautifulSoup(resp.text, "html.parser")
    for a in soup2.find_all("a", href=True):
        h = a["href"].strip()
        if h.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full = norm(urljoin(url, h))
        if urlparse(full).netloc.replace("www.", "") == host.replace("www.", ""):
            d["links"].append(full)
    d["mixed"] = (url.startswith("https") and bool(re.search(r'(src|href)="http://', resp.text)))
    return d

def crawl_site(cfg, conn, site):
    s = cfg["settings"]
    base = site.get("base_url") or f"https://{site['domain']}"
    host = urlparse(base).netloc
    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    start = base.rstrip("/") + "/"
    sm_urls = []
    for sm in site.get("sitemaps", []):
        sm_urls += fetch_sitemap_urls(sess, sm)
    in_sitemap = set(sm_urls)
    queue, seen = deque(), set()
    for u in [norm(start)] + sm_urls:
        if u not in seen:
            seen.add(u); queue.append((u, 0))
    results, outlinks = {}, defaultdict(set)
    max_pages, delay = s["crawl_max_pages"], s["crawl_delay_seconds"]
    started = dt.datetime.now().isoformat(timespec="seconds")

    def work(item):
        url, depth = item
        time.sleep(delay)
        try:
            t0 = time.time()
            r = sess.get(url, timeout=25, allow_redirects=False)
            ms = int((time.time() - t0) * 1000)
            d = analyse(url, r, ms, depth, host)
            if r.status_code in (301, 302, 307, 308):
                d["redirect_to"] = norm(urljoin(url, r.headers.get("Location", "")))
            return d
        except Exception as ex:
            return {"url": url, "status": 0, "ms": 0, "error": str(ex)[:120], "depth": depth, "links": [],
                    "title": "", "meta_desc": "", "h1_count": 0, "canonical": "", "noindex": 0, "words": 0,
                    "imgs_no_alt": 0, "schema_types": ""}

    with ThreadPoolExecutor(max_workers=4) as ex:
        while queue and len(results) < max_pages:
            batch = []
            while queue and len(batch) < 16 and len(results) + len(batch) < max_pages:
                batch.append(queue.popleft())
            for d in ex.map(work, batch):
                results[d["url"]] = d
                targets = list(d["links"])
                if d.get("redirect_to"):
                    targets.append(d["redirect_to"])
                for l in targets:
                    if d["links"] and l in d["links"]:
                        outlinks[d["url"]].add(l)
                    if l not in seen and not SKIP_EXT.search(l) and not SKIP_PATH.search(l):
                        seen.add(l); queue.append((l, d["depth"] + 1))

    # inlinks
    inlinks = defaultdict(int)
    for src, ls in outlinks.items():
        for l in ls:
            inlinks[l] += 1
    # issues
    issues = []
    add = lambda url, typ, sev, detail="": issues.append((url, typ, sev, detail))
    titles, descs = defaultdict(list), defaultdict(list)
    for url, d in results.items():
        st = d["status"]
        if st == 0: add(url, "Unreachable", "error", d.get("error", ""))
        elif st >= 500: add(url, "Server error (5xx)", "error", str(st))
        elif st >= 400: add(url, "Broken page (4xx)", "error", str(st))
        elif st in (301, 302, 307, 308):
            tgt = results.get(d.get("redirect_to", ""))
            if tgt and tgt["status"] in (301, 302, 307, 308):
                add(url, "Redirect chain", "warning", f"{url} -> {d['redirect_to']} -> {tgt.get('redirect_to','')}")
            else:
                add(url, "Redirect", "notice", f"-> {d.get('redirect_to','')}")
            if url in in_sitemap: add(url, "Redirecting URL in sitemap", "warning", "")
        if st == 200 and d.get("is_html"):
            if not d["title"]: add(url, "Missing title", "error")
            else:
                titles[d["title"]].append(url)
                if len(d["title"]) > 65: add(url, "Title too long", "warning", f"{len(d['title'])} chars")
                if len(d["title"]) < 20: add(url, "Title too short", "notice", f"{len(d['title'])} chars")
            if not d["meta_desc"]: add(url, "Missing meta description", "warning")
            else:
                descs[d["meta_desc"]].append(url)
                if len(d["meta_desc"]) > 165: add(url, "Meta description too long", "notice", f"{len(d['meta_desc'])} chars")
            if d["h1_count"] == 0: add(url, "Missing H1", "warning")
            if d["h1_count"] > 1: add(url, "Multiple H1", "notice", str(d["h1_count"]))
            if not d["canonical"]: add(url, "Missing canonical", "notice")
            elif norm(d["canonical"]) != url and not d["noindex"]:
                add(url, "Canonical points elsewhere", "notice", d["canonical"])
            if d["noindex"] and url in in_sitemap: add(url, "Noindex page in sitemap", "error")
            if d["words"] < 150 and not d["noindex"]: add(url, "Thin content", "notice", f"{d['words']} words")
            if d["imgs_no_alt"]: add(url, "Images without alt text", "notice", f"{d['imgs_no_alt']} images")
            if d["ms"] > 1500: add(url, "Slow response", "warning", f"{d['ms']} ms")
            if d.get("mixed"): add(url, "Mixed content (http resources)", "warning")
            if not d.get("viewport", True): add(url, "Missing viewport meta", "warning")
            if not d.get("og", True): add(url, "Missing Open Graph tags", "notice")
            if inlinks[url] == 0 and url != norm(start) and not d["noindex"]:
                add(url, "Orphan page (no internal links)", "warning")
            if url not in in_sitemap and sm_urls and not d["noindex"] and d["canonical"] in ("", url):
                add(url, "Indexable page not in sitemap", "notice")
            for l in outlinks.get(url, ()):
                t = results.get(l)
                if t and t["status"] >= 400:
                    add(url, "Broken internal link", "error", f"{l} ({t['status']})")
    for t, us in titles.items():
        if len(us) > 1:
            for u in us: add(u, "Duplicate title", "warning", f"shared with {len(us)-1} other page(s)")
    for t, us in descs.items():
        if len(us) > 1:
            for u in us: add(u, "Duplicate meta description", "notice", f"shared with {len(us)-1} other page(s)")
    w = {"error": 3, "warning": 1, "notice": 0.2}
    npages = max(len(results), 1)
    per_page = sum(w[i[2]] for i in issues) / npages      # weighted issues per page
    score = round(100 / (1 + per_page), 1)
    cur = conn.execute("INSERT INTO crawl_runs (site,started,finished,pages,score,meta) VALUES (?,?,?,?,?,?)",
                       (site["id"], started, dt.datetime.now().isoformat(timespec="seconds"), len(results), score,
                        json.dumps({"sitemap_urls": len(in_sitemap)})))
    rid = cur.lastrowid
    for url, d in results.items():
        conn.execute("INSERT INTO crawl_pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, url, d["status"], d["ms"], d["title"], d["meta_desc"], d["h1_count"], d["canonical"],
                      d["noindex"], d["words"], d["imgs_no_alt"], inlinks[url], int(url in in_sitemap),
                      d["schema_types"], d["depth"]))
    conn.executemany("INSERT INTO crawl_issues VALUES (?,?,?,?,?)", [(rid,) + i for i in issues])
    conn.commit()
    log(conn, site["id"], "crawl", True, f"{len(results)} pages, {len(issues)} issues, score {score}")
    geo_checks(conn, site, sess, rid)
    return rid

AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-User", "Claude-SearchBot",
           "PerplexityBot", "Perplexity-User", "Google-Extended", "Applebot-Extended", "CCBot", "Bytespider", "Amazonbot"]

def parse_robots(txt):
    groups, agents, last_rule = {}, [], False
    for line in txt.splitlines():
        line = line.split("#")[0].strip()
        if ":" not in line:
            continue
        k, v = [x.strip() for x in line.split(":", 1)]
        k = k.lower()
        if k == "user-agent":
            if last_rule:
                agents = []
            agents.append(v.lower())
            groups.setdefault(v.lower(), [])
            last_rule = False
        elif k in ("allow", "disallow"):
            for a in agents:
                groups[a].append((k, v))
            last_rule = True
    return groups

def geo_checks(conn, site, sess, rid):
    """AI-search readiness: robots.txt rules for AI bots, llms.txt, structured data coverage."""
    base = (site.get("base_url") or f"https://{site['domain']}").rstrip("/")
    sid = site["id"]
    now = dt.datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM geo_checks WHERE site=?", (sid,))
    rows = []
    try:
        r = sess.get(f"{base}/robots.txt", timeout=15)
        g = parse_robots(r.text) if r.status_code == 200 else {}
        for bot in AI_BOTS:
            rules = g.get(bot.lower()) or g.get("*") or []
            blocked = any(k == "disallow" and v.strip() == "/" for k, v in rules)
            specific = bot.lower() in g
            rows.append((sid, now, f"bot:{bot}", "blocked" if blocked else "allowed",
                         "explicit rule" if specific else "inherits * rules"))
    except Exception as ex:
        rows.append((sid, now, "robots.txt", "error", str(ex)[:100]))
    try:
        r = sess.get(f"{base}/llms.txt", timeout=15)
        rows.append((sid, now, "llms.txt", "present" if r.status_code == 200 and len(r.text) > 20 else "missing", ""))
    except Exception:
        rows.append((sid, now, "llms.txt", "missing", ""))
    pages = conn.execute("SELECT schema_types, words FROM crawl_pages WHERE run_id=? AND status=200 AND noindex=0", (rid,)).fetchall()
    n = max(len(pages), 1)
    for key, pat in (("schema:Product", "Product"), ("schema:FAQPage", "FAQPage"), ("schema:Organization", "Organization"),
                     ("schema:BreadcrumbList", "BreadcrumbList"), ("schema:Article", "Article")):
        c = sum(1 for p in pages if pat in (p["schema_types"] or ""))
        rows.append((sid, now, key, f"{round(100*c/n)}%", f"{c} of {len(pages)} indexable pages"))
    conn.executemany("INSERT INTO geo_checks VALUES (?,?,?,?,?)", rows)
    conn.commit()
