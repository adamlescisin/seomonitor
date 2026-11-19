"""Magic-link authentication, users and permissions for SEO Compass.

Design notes
- Passwordless: a one-time link is emailed; opening it shows a confirm page, and only a POST from that page
  consumes the token (so mail-scanner prefetching can't burn or hijack it).
- Tokens and session ids are random 256-bit values; only their SHA-256 hashes are stored.
- Login requests never reveal whether an address is authorised.
- Admins are listed in config.yaml (auth.admins); clients are managed in the Settings screen.
"""
import secrets, hashlib, time, json, smtplib, ssl, threading, re
from email.message import EmailMessage
from email.utils import formataddr
from collections import defaultdict, deque

SECTIONS = ["overview", "queries", "pages", "opps", "audit", "ai", "ideas"]
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_rl = defaultdict(deque)
_rl_lock = threading.Lock()

def h(t): return hashlib.sha256(t.encode()).hexdigest()
def now(): return int(time.time())
def norm_email(e): return (e or "").strip().lower()
def valid_email(e): return bool(EMAIL_RE.match(e or "")) and len(e) <= 254

def rate_ok(key, limit, window=900):
    """Sliding-window limiter, in memory (resets on restart; fine for a single-process app)."""
    t = time.time()
    with _rl_lock:
        q = _rl[key]
        while q and q[0] < t - window: q.popleft()
        if len(q) >= limit: return False
        q.append(t); return True

def log_access(conn, email, event, ip="", detail=""):
    conn.execute("INSERT INTO access_log VALUES (?,?,?,?,?)", (now(), email, event, ip, detail[:200])); conn.commit()

# ------------------------------------------------------------ users
def ensure_admins(cfg, conn):
    for e in cfg["auth"]["admins"]:
        conn.execute("INSERT OR IGNORE INTO users (email,name,role,sites,sections,show_revenue,active,created) VALUES (?,?,?,?,?,1,1,?)",
                     (e, "", "admin", '["*"]', json.dumps(SECTIONS), now()))
        conn.execute("UPDATE users SET role='admin', active=1 WHERE email=?", (e,))
    conn.commit()

def row_to_user(r, cfg):
    if not r: return None
    admin = r["role"] == "admin" or r["email"] in cfg["auth"]["admins"]
    u = {"email": r["email"], "name": r["name"] or "", "role": "admin" if admin else "client", "active": bool(r["active"]),
         "sites": ["*"] if admin else json.loads(r["sites"] or "[]"),
         "sections": SECTIONS + ["settings"] if admin else [s for s in json.loads(r["sections"] or "[]") if s in SECTIONS],
         "show_revenue": True if admin else bool(r["show_revenue"]),
         "last_login": r["last_login"], "config_admin": r["email"] in cfg["auth"]["admins"]}
    return u

def get_user(conn, cfg, email):
    return row_to_user(conn.execute("SELECT * FROM users WHERE email=?", (norm_email(email),)).fetchone(), cfg)

def can_site(user, site_id): return "*" in user["sites"] or site_id in user["sites"]
def can_section(user, sec): return sec in user["sections"]

def save_user(conn, cfg, email, name, sites, sections, show_revenue, active):
    email = norm_email(email)
    if not valid_email(email): raise ValueError("invalid email")
    known = {s["id"] for s in cfg["sites"]}
    sites = [s for s in sites if s in known]
    sections = [s for s in sections if s in SECTIONS]
    exists = conn.execute("SELECT role FROM users WHERE email=?", (email,)).fetchone()
    if exists and (exists["role"] == "admin" or email in cfg["auth"]["admins"]):
        raise ValueError("admins are managed in config.yaml")
    if exists:
        conn.execute("UPDATE users SET name=?, sites=?, sections=?, show_revenue=?, active=? WHERE email=?",
                     (name.strip()[:100], json.dumps(sites), json.dumps(sections), int(show_revenue), int(active), email))
    else:
        conn.execute("INSERT INTO users (email,name,role,sites,sections,show_revenue,active,created) VALUES (?,?,?,?,?,?,?,?)",
                     (email, name.strip()[:100], "client", json.dumps(sites), json.dumps(sections), int(show_revenue), int(active), now()))
    if not active: revoke_sessions(conn, email)
    conn.commit()

def delete_user(conn, cfg, email):
    email = norm_email(email)
    if email in cfg["auth"]["admins"]: raise ValueError("admins are managed in config.yaml")
    conn.execute("DELETE FROM users WHERE email=?", (email,)); revoke_sessions(conn, email)
    conn.execute("DELETE FROM login_tokens WHERE email=?", (email,)); conn.commit()

# ------------------------------------------------------------ tokens & sessions
def create_login_token(conn, cfg, email):
    raw = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM login_tokens WHERE expires < ? OR email=?", (now() - 86400, email))   # one live link per user
    conn.execute("INSERT INTO login_tokens VALUES (?,?,?,0)", (h(raw), email, now() + cfg["auth"]["token_minutes"] * 60))
    conn.commit()
    return raw

def consume_token(conn, raw):
    """Atomically mark single-use token as used. Returns email or None."""
    if not raw or len(raw) > 200: return None
    cur = conn.execute("UPDATE login_tokens SET used=1 WHERE hash=? AND used=0 AND expires>?", (h(raw), now()))
    conn.commit()
    if cur.rowcount != 1: return None
    return conn.execute("SELECT email FROM login_tokens WHERE hash=?", (h(raw),)).fetchone()["email"]

def create_session(conn, cfg, email, ua="", ip=""):
    raw = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires < ?", (now(),))
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (h(raw), email, now(), now() + cfg["auth"]["session_days"] * 86400, ua[:200], ip))
    conn.execute("UPDATE users SET last_login=? WHERE email=?", (now(), email)); conn.commit()
    return raw

def session_user(conn, cfg, raw):
    if not raw or len(raw) > 200: return None
    s = conn.execute("SELECT email FROM sessions WHERE hash=? AND expires>?", (h(raw), now())).fetchone()
    if not s: return None
    u = get_user(conn, cfg, s["email"])
    return u if u and u["active"] else None

def revoke_sessions(conn, email): conn.execute("DELETE FROM sessions WHERE email=?", (norm_email(email),)); conn.commit()
def end_session(conn, raw):
    if raw: conn.execute("DELETE FROM sessions WHERE hash=?", (h(raw),)); conn.commit()
def session_count(conn, email):
    return conn.execute("SELECT COUNT(*) c FROM sessions WHERE email=? AND expires>?", (email, now())).fetchone()["c"]

# ------------------------------------------------------------ email
def smtp_configured(cfg): s = cfg.get("smtp") or {}; return bool(s.get("host") and s.get("from"))

def send_mail(cfg, to, subject, text, html):
    s = cfg["smtp"]
    msg = EmailMessage()
    msg["Subject"] = subject; msg["To"] = to
    msg["From"] = formataddr((s.get("from_name", "SEO Compass"), s["from"]))
    msg.set_content(text); msg.add_alternative(html, subtype="html")
    sec, port = (s.get("security") or "ssl").lower(), int(s.get("port") or 0)
    ctx = ssl.create_default_context()
    if sec == "ssl":
        srv = smtplib.SMTP_SSL(s["host"], port or 465, context=ctx, timeout=20)
    else:
        srv = smtplib.SMTP(s["host"], port or 587, timeout=20)
        if sec == "starttls": srv.starttls(context=ctx)
    try:
        if s.get("user"): srv.login(s["user"], s.get("password", ""))
        srv.send_message(msg)
    finally:
        srv.quit()

def login_url(cfg, raw): return cfg["auth"]["base_url"].rstrip("/") + "/auth/verify?token=" + raw

def compose_login_mail(cfg, name, link, invite):
    mins = cfg["auth"]["token_minutes"]
    hello = f"Dobrý den{' ' + name if name else ''},"
    if invite: intro = "byl vám zpřístupněn přehled výkonu webu v nástroji SEO Compass."
    else: intro = "pro přihlášení do nástroje SEO Compass použijte tlačítko níže."
    text = f"{hello}\n\n{intro}\n\nPřihlásit se: {link}\n\nOdkaz platí {mins} minut a lze jej použít jen jednou. Pokud jste o přihlášení nežádali, tento e-mail ignorujte.\n"
    html = f"""<div style="font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:480px;margin:auto;padding:24px;color:#0b0b0b">
<h2 style="margin:0 0 12px">SEO Compass</h2><p>{hello}</p><p>{intro}</p>
<p style="margin:24px 0"><a href="{link}" style="background:#2a78d6;color:#fff;text-decoration:none;padding:12px 22px;border-radius:9px;font-weight:600;display:inline-block">Přihlásit se</a></p>
<p style="color:#52514e;font-size:13px">Odkaz platí {mins} minut a lze jej použít jen jednou. Pokud jste o přihlášení nežádali, tento e-mail ignorujte.</p>
<p style="color:#898781;font-size:12px;word-break:break-all">Nefunguje tlačítko? Zkopírujte adresu: {link}</p></div>"""
    return "Přihlášení do SEO Compass", text, html

def send_login_link(cfg, conn, user, invite=False):
    """Returns (sent: bool, link: str). If SMTP isn't configured the link is returned/printed instead of sent."""
    raw = create_login_token(conn, cfg, user["email"])
    link = login_url(cfg, raw)
    if not smtp_configured(cfg):
        print(f"[SEO Compass] SMTP not configured. Login link for {user['email']}: {link}", flush=True)
        return False, link
    subj, text, html = compose_login_mail(cfg, user["name"], link, invite)
    send_mail(cfg, user["email"], subj, text, html)
    return True, link
