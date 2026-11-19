import sqlite3, os, yaml, threading

ROOT = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()

def load_config(path=None):
    path = path or os.environ.get("SEO_COMPASS_CONFIG") or os.path.join(ROOT, "config.yaml")
    if not os.path.exists(path):
        path = os.path.join(ROOT, "config.example.yaml")
    with open(path) as f:
        cfg = yaml.safe_load(f)
    base = os.path.dirname(os.path.abspath(path))
    s = cfg.setdefault("settings", {})
    s.setdefault("db_path", "./seo_compass.db")
    s.setdefault("min_impressions", 30)
    s.setdefault("gsc_backfill_days", 480)
    s.setdefault("crawl_max_pages", 600)
    s.setdefault("crawl_delay_seconds", 0.2)
    s.setdefault("port", 8765)
    for k in ("db_path",):
        if not os.path.isabs(s[k]):
            s[k] = os.path.join(base, s[k])
    a = cfg.setdefault("auth", {})
    a.setdefault("admins", [])
    a["admins"] = [str(x).strip().lower() for x in (a["admins"] or []) if x]
    a.setdefault("base_url", "http://localhost:%s" % s["port"])
    a.setdefault("session_days", 30)
    a.setdefault("token_minutes", 15)
    a.setdefault("trust_proxy", False)
    cfg.setdefault("smtp", {})
    g = cfg.setdefault("google", {})
    sa = g.get("service_account_file")
    if sa and not os.path.isabs(sa):
        g["service_account_file"] = os.path.join(base, sa)
    return cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS gsc_daily (site TEXT, date TEXT, clicks INT, impressions INT, ctr REAL, position REAL, PRIMARY KEY(site,date));
CREATE TABLE IF NOT EXISTS gsc_dim (site TEXT, date TEXT, dim TEXT, value TEXT, clicks INT, impressions INT, position REAL, PRIMARY KEY(site,date,dim,value));
CREATE TABLE IF NOT EXISTS gsc_qp (site TEXT, date TEXT, query TEXT, page TEXT, clicks INT, impressions INT, position REAL, PRIMARY KEY(site,date,query,page));
CREATE INDEX IF NOT EXISTS ix_qp_site_date ON gsc_qp(site,date);
CREATE INDEX IF NOT EXISTS ix_qp_query ON gsc_qp(site,query);
CREATE TABLE IF NOT EXISTS ga_daily (site TEXT, date TEXT, channel TEXT, source TEXT, medium TEXT, sessions INT, users INT, engaged INT, conversions REAL, revenue REAL, PRIMARY KEY(site,date,channel,source,medium));
CREATE TABLE IF NOT EXISTS ga_landing (site TEXT, date TEXT, landing TEXT, channel TEXT, source TEXT, sessions INT, engaged INT, conversions REAL, revenue REAL, PRIMARY KEY(site,date,landing,channel,source));
CREATE TABLE IF NOT EXISTS crawl_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT, started TEXT, finished TEXT, pages INT, score REAL, meta TEXT);
CREATE TABLE IF NOT EXISTS crawl_pages (run_id INT, url TEXT, status INT, ms INT, title TEXT, meta_desc TEXT, h1_count INT, canonical TEXT, noindex INT, words INT, imgs_no_alt INT, inlinks INT, in_sitemap INT, schema_types TEXT, depth INT);
CREATE INDEX IF NOT EXISTS ix_cp_run ON crawl_pages(run_id);
CREATE TABLE IF NOT EXISTS crawl_issues (run_id INT, url TEXT, type TEXT, severity TEXT, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_ci_run ON crawl_issues(run_id);
CREATE TABLE IF NOT EXISTS keyword_ideas (site TEXT, idea TEXT, seed TEXT, kind TEXT, fetched TEXT, PRIMARY KEY(site,idea));
CREATE TABLE IF NOT EXISTS sync_log (ts TEXT, site TEXT, job TEXT, ok INT, message TEXT);
CREATE TABLE IF NOT EXISTS users (email TEXT PRIMARY KEY, name TEXT DEFAULT '', role TEXT DEFAULT 'client', sites TEXT DEFAULT '[]', sections TEXT DEFAULT '[]', show_revenue INT DEFAULT 0, active INT DEFAULT 1, created INT, last_login INT);
CREATE TABLE IF NOT EXISTS login_tokens (hash TEXT PRIMARY KEY, email TEXT, expires INT, used INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, email TEXT, created INT, expires INT, ua TEXT, ip TEXT);
CREATE INDEX IF NOT EXISTS ix_sess_email ON sessions(email);
CREATE TABLE IF NOT EXISTS access_log (ts INT, email TEXT, event TEXT, ip TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS geo_checks (site TEXT, ts TEXT, key TEXT, status TEXT, detail TEXT);
"""

def connect(cfg):
    conn = sqlite3.connect(cfg["settings"]["db_path"], check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn

def log(conn, site, job, ok, message):
    with _lock:
        conn.execute("INSERT INTO sync_log VALUES (datetime('now'),?,?,?,?)", (site, job, int(ok), message[:500]))
        conn.commit()

def q(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
