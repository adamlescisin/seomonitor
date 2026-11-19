#!/usr/bin/env python3
"""SEO Compass - self-hosted SEO + GEO dashboard.  Usage:
   python app.py                 start dashboard (http://localhost:8765)
   python app.py --demo          start with synthetic demo data
   python app.py sync [site]     pull GSC + GA4 now (use from cron/launchd)
   python app.py crawl [site]    run site audit crawl
   python app.py ideas [site]    refresh keyword ideas
   python app.py daily           sync + crawl (weekly) + ideas (weekly)
"""
import sys, os, threading, datetime as dt, argparse, urllib.parse
from flask import Flask, jsonify, request, send_from_directory, redirect, g
import db, insights, auth

ap = argparse.ArgumentParser()
ap.add_argument("cmd", nargs="?", default="serve")
ap.add_argument("site", nargs="?")
ap.add_argument("--demo", action="store_true")
ap.add_argument("--config")
args, _ = ap.parse_known_args()

cfg = db.load_config(args.config)
if args.demo:
    cfg["settings"]["db_path"] = os.path.join(db.ROOT, "demo.db")
conn = db.connect(cfg)                      # used by CLI + startup only; web requests use C()
SITES = {s["id"]: s for s in cfg["sites"]}
AUTH_ON = bool(cfg["auth"]["admins"])
if AUTH_ON: auth.ensure_admins(cfg, conn)
COOKIE = "sc_session"
HTTPS = cfg["auth"]["base_url"].lower().startswith("https://")
OPEN_ADMIN = {"email": "local", "name": "", "role": "admin", "active": True, "sites": ["*"], "sections": auth.SECTIONS + ["settings"],
              "show_revenue": True, "config_admin": True, "last_login": None}

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
_local = threading.local()
def C():
    """One SQLite connection per request thread (Flask serves requests concurrently)."""
    if not hasattr(_local, "c"):
        _local.c = db.connect(cfg)
    return _local.c

jobs = {}   # "site:job" -> state

# ------------------------------------------------------------------ security plumbing
def client_ip():
    if cfg["auth"]["trust_proxy"]:
        return request.headers.get("CF-Connecting-IP") or (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()) or request.remote_addr
    return request.remote_addr or ""

PUBLIC = {"login_page", "verify_page", "auth_request", "auth_verify", "chartjs", "favicon"}

@app.before_request
def gate():
    g.user = None
    if request.method not in ("GET", "HEAD", "OPTIONS"):       # CSRF: custom header + same-origin check
        if request.headers.get("X-Requested-With") != "sc": return jsonify(error="forbidden"), 403
        origin = request.headers.get("Origin")
        if origin:
            allowed = {request.host, urllib.parse.urlparse(cfg["auth"]["base_url"]).netloc}
            if urllib.parse.urlparse(origin).netloc not in allowed: return jsonify(error="forbidden"), 403
    if not AUTH_ON:
        g.user = OPEN_ADMIN; return
    if request.endpoint in PUBLIC: return
    g.user = auth.session_user(C(), cfg, request.cookies.get(COOKIE))
    if not g.user:
        if request.path.startswith("/api/"): return jsonify(error="auth"), 401
        return redirect("/login")

@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "no-referrer"
    r.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if request.path.startswith(("/api/", "/auth/")) or request.endpoint in ("index", "login_page", "verify_page"):
        r.headers["Cache-Control"] = "no-store"
    return r

def need(section):
    def deco(fn):
        import functools
        @functools.wraps(fn)
        def w(*a, **k):
            if not auth.can_section(g.user, section): raise PermissionError(section)
            return fn(*a, **k)
        return w
    return deco

def admin_only(fn):
    import functools
    @functools.wraps(fn)
    def w(*a, **k):
        if g.user["role"] != "admin": raise PermissionError("admin")
        return fn(*a, **k)
    return w

REV_KEYS = ("revenue",)
def scrub(x):
    """Clients without the revenue privilege never receive revenue figures at all (not just hidden in the UI)."""
    if g.user["show_revenue"]: return x
    if isinstance(x, dict): return {k: scrub(v) for k, v in x.items() if not any(r in k for r in REV_KEYS)}
    if isinstance(x, list): return [scrub(v) for v in x]
    return x
def J(x): return jsonify(scrub(x))

def sid():
    s = request.args.get("site") or next((k for k in SITES if auth.can_site(g.user, k)), None)
    if s not in SITES: raise KeyError(s)
    if not auth.can_site(g.user, s): raise PermissionError(s)
    return s

def days(): return min(max(int(request.args.get("days", 28)), 7), 480)

@app.errorhandler(KeyError)
def e404(e): return jsonify({"error": "not found"}), 404
@app.errorhandler(PermissionError)
def e403(e): return jsonify({"error": "forbidden"}), 403
@app.errorhandler(ValueError)
def e400(e): return jsonify({"error": str(e)}), 400

# ------------------------------------------------------------------ pages
def page(name): return send_from_directory(os.path.join(db.ROOT, "templates"), name)
@app.get("/")
def index(): return page("index.html")
@app.get("/login")
def login_page():
    if AUTH_ON and auth.session_user(C(), cfg, request.cookies.get(COOKIE)): return redirect("/")
    return page("auth.html")
@app.get("/auth/verify")
def verify_page(): return page("auth.html")
@app.get("/chart.umd.js")
def chartjs(): return page("chart.umd.js")
@app.get("/favicon.ico")
def favicon(): return "", 204

# ------------------------------------------------------------------ auth endpoints
def _send_async(user, invite=False):
    def go():
        c = db.connect(cfg)
        try: auth.send_login_link(cfg, c, user, invite)
        except Exception as ex: auth.log_access(c, user["email"], "mail_error", "", repr(ex))
    threading.Thread(target=go, daemon=True).start()

@app.post("/auth/request")
def auth_request():
    email = auth.norm_email((request.get_json(silent=True) or {}).get("email"))
    ip = client_ip()
    allowed = auth.valid_email(email) and auth.rate_ok("ip:" + ip, 10) and auth.rate_ok("em:" + email, 3)
    u = auth.get_user(C(), cfg, email) if allowed else None
    if u and u["active"]:
        auth.log_access(C(), email, "link_requested", ip)
        _send_async(u)
    elif allowed:
        auth.log_access(C(), email, "link_denied_unknown", ip)
    return jsonify(ok=True)          # identical response whether or not the address is authorised

@app.post("/auth/verify")
def auth_verify():
    ip = client_ip()
    if not auth.rate_ok("v:" + ip, 20): return jsonify(ok=False), 429
    email = auth.consume_token(C(), (request.get_json(silent=True) or {}).get("token"))
    u = auth.get_user(C(), cfg, email) if email else None
    if not u or not u["active"]:
        auth.log_access(C(), email or "", "verify_failed", ip); return jsonify(ok=False), 400
    raw = auth.create_session(C(), cfg, email, request.headers.get("User-Agent", ""), ip)
    auth.log_access(C(), email, "login", ip)
    resp = jsonify(ok=True)
    resp.set_cookie(COOKIE, raw, max_age=cfg["auth"]["session_days"] * 86400, httponly=True, samesite="Lax", secure=HTTPS, path="/")
    return resp

@app.post("/auth/logout")
def auth_logout():
    auth.end_session(C(), request.cookies.get(COOKIE))
    resp = jsonify(ok=True); resp.delete_cookie(COOKIE, path="/"); return resp

@app.get("/api/me")
def api_me():
    u = dict(g.user)
    u["auth_enabled"] = AUTH_ON; u["demo"] = args.demo
    u["site_list"] = [{"id": s["id"], "name": s["name"], "domain": s["domain"]} for s in cfg["sites"] if auth.can_site(g.user, s["id"])]
    return jsonify(u)

# ------------------------------------------------------------------ data endpoints
@app.get("/api/overview")
@need("overview")
def api_overview(): return J(insights.overview(C(), sid(), days()))
@app.get("/api/timeseries")
@need("overview")
def api_ts(): return J(insights.timeseries(C(), sid(), days()))
@app.get("/api/breakdown")
@need("overview")
def api_break(): return J({d: insights.breakdown(C(), sid(), days(), d) for d in ("country", "device")})
@app.get("/api/queries")
@need("queries")
def api_queries(): return J(insights.queries(C(), sid(), days(), request.args.get("search", "")))
@app.get("/api/pages")
@need("pages")
def api_pages(): return J(insights.pages(C(), sid(), days(), request.args.get("search", "")))
@app.get("/api/opportunities")
@need("opps")
def api_opps(): return J(insights.opportunities(C(), sid(), days(), cfg["settings"]["min_impressions"]))
@app.get("/api/ai")
@need("ai")
def api_ai(): return J(insights.ai_report(C(), sid(), days()))
@app.get("/api/audit")
@need("audit")
def api_audit(): return J(insights.audit(C(), sid()))
@app.get("/api/audit/detail")
@need("audit")
def api_audit_detail(): return J(insights.audit_detail(C(), sid(), request.args.get("type", "")))
@app.get("/api/crawl/pages")
@need("audit")
def api_crawl_pages(): return J(insights.crawl_pages(C(), sid()))
@app.get("/api/ideas")
@need("ideas")
def api_ideas(): return J(insights.ideas(C(), sid()))

# ------------------------------------------------------------------ jobs (admin)
def run_job(site_id, name, fn):
    key = f"{site_id}:{name}"
    if jobs.get(key, {}).get("state") == "running": return False
    jobs[key] = {"state": "running", "msg": "", "started": dt.datetime.now().isoformat(timespec="seconds")}
    def target():
        c = db.connect(cfg)
        try:
            jobs[key] = {"state": "done", "msg": str(fn(c, SITES[site_id]))[:300]}
        except Exception as ex:
            db.log(c, site_id, name, False, repr(ex)); jobs[key] = {"state": "error", "msg": repr(ex)[:300]}
    threading.Thread(target=target, daemon=True).start()
    return True

def job_sync(c, site):
    import sync_google; return sync_google.sync_all(cfg, c, site)
def job_crawl(c, site):
    import crawler; return crawler.crawl_site(cfg, c, site)
def job_ideas(c, site):
    import keywords; return keywords.fetch_ideas(cfg, c, site)
JOBS = {"sync": job_sync, "crawl": job_crawl, "ideas": job_ideas}

@app.get("/api/status")
@admin_only
def api_status():
    s = sid()
    return jsonify({"jobs": {k: v for k, v in jobs.items() if k.startswith(s + ":")}, "log": db.q(C(), "SELECT * FROM sync_log WHERE site=? ORDER BY ts DESC LIMIT 8", (s,))})

@app.post("/api/run/<job>")
@admin_only
def api_run(job):
    if job not in JOBS or args.demo: return jsonify({"started": False, "reason": "demo mode" if args.demo else "unknown job"}), 400
    return jsonify({"started": run_job(sid(), job, JOBS[job])})

# ------------------------------------------------------------------ admin: users & access
def _user_out(u, c):
    u = dict(u); u["sessions"] = auth.session_count(c, u["email"]); return u

@app.get("/api/admin/info")
@admin_only
def admin_info():
    return jsonify({"auth_enabled": AUTH_ON, "smtp": auth.smtp_configured(cfg), "base_url": cfg["auth"]["base_url"], "https": HTTPS,
                    "admins": cfg["auth"]["admins"], "session_days": cfg["auth"]["session_days"], "token_minutes": cfg["auth"]["token_minutes"],
                    "sites": [{"id": s["id"], "name": s["name"]} for s in cfg["sites"]], "sections": auth.SECTIONS})

@app.get("/api/admin/users")
@admin_only
def admin_users():
    c = C()
    rows = [auth.row_to_user(r, cfg) for r in c.execute("SELECT * FROM users ORDER BY role, email").fetchall()]
    return jsonify([_user_out(u, c) for u in rows])

@app.post("/api/admin/users")
@admin_only
def admin_save_user():
    d = request.get_json(silent=True) or {}
    auth.save_user(C(), cfg, d.get("email"), d.get("name", ""), list(d.get("sites") or []), list(d.get("sections") or []),
                   bool(d.get("show_revenue")), bool(d.get("active", True)))
    auth.log_access(C(), g.user["email"], "user_saved", client_ip(), auth.norm_email(d.get("email")))
    out = {"ok": True}
    if d.get("invite"):
        out.update(_send_link(auth.get_user(C(), cfg, d["email"]), True))
    return jsonify(out)

def _send_link(u, invite):
    try:
        sent, link = auth.send_login_link(cfg, C(), u, invite)
    except Exception as ex:
        return {"sent": False, "error": f"E-mail se nepodařilo odeslat / could not send: {ex}"}
    return {"sent": sent, "link": None if sent else link}

@app.post("/api/admin/users/link")
@admin_only
def admin_link():
    u = auth.get_user(C(), cfg, (request.get_json(silent=True) or {}).get("email"))
    if not u or not u["active"]: raise KeyError("user")
    auth.log_access(C(), g.user["email"], "link_sent_by_admin", client_ip(), u["email"])
    return jsonify(_send_link(u, False))

@app.post("/api/admin/users/revoke")
@admin_only
def admin_revoke():
    e = auth.norm_email((request.get_json(silent=True) or {}).get("email"))
    auth.revoke_sessions(C(), e); auth.log_access(C(), g.user["email"], "sessions_revoked", client_ip(), e); return jsonify(ok=True)

@app.post("/api/admin/users/delete")
@admin_only
def admin_delete():
    e = auth.norm_email((request.get_json(silent=True) or {}).get("email"))
    if e == g.user["email"]: raise ValueError("cannot delete yourself")
    auth.delete_user(C(), cfg, e); auth.log_access(C(), g.user["email"], "user_deleted", client_ip(), e); return jsonify(ok=True)

@app.get("/api/admin/log")
@admin_only
def admin_log():
    return jsonify(db.q(C(), "SELECT ts, email, event, ip, detail FROM access_log ORDER BY ts DESC, rowid DESC LIMIT 60"))

@app.post("/api/admin/test-email")
@admin_only
def admin_test_email():
    if not auth.smtp_configured(cfg): return jsonify({"sent": False, "error": "SMTP is not configured"}), 400
    try:
        auth.send_mail(cfg, g.user["email"], "SEO Compass: test e-mailu", "Odesílání e-mailů funguje.", "<p>Odesílání e-mailů funguje.</p>")
    except Exception as ex:
        return jsonify({"sent": False, "error": str(ex)}), 502
    return jsonify({"sent": True})

@app.post("/api/admin/restart")
@admin_only
def admin_restart():
    """Re-exec the process so config.yaml is re-read. The PID stays the same, so launchd is not disturbed."""
    if any(v.get("state") == "running" for v in jobs.values()):
        return jsonify({"restarting": False, "error": "running"}), 409
    db.log(C(), "-", "restart", True, g.user["email"])
    def _go():
        import time; time.sleep(0.6)               # let the response reach the browser
        for fd in range(3, 1024):                   # make sure the listening socket is released on exec
            try: os.set_inheritable(fd, False)
            except OSError: pass
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=_go, daemon=True).start()
    return jsonify({"restarting": True})

# ------------------------------------------------------------------ CLI / main
def cli(cmd, only):
    targets = [SITES[only]] if only else cfg["sites"]
    for s in targets:
        print(f"== {s['name']}: {cmd}")
        if cmd == "sync": print(job_sync(conn, s))
        elif cmd == "crawl": print(job_crawl(conn, s))
        elif cmd == "ideas": print(job_ideas(conn, s))
        elif cmd == "daily":
            print(job_sync(conn, s))
            if dt.date.today().weekday() == 0:       # Mondays: crawl + refresh ideas
                print(job_crawl(conn, s)); print(job_ideas(conn, s))

if __name__ == "__main__":
    if args.demo:
        import demo; demo.ensure(cfg, conn)
    if args.cmd in ("sync", "crawl", "ideas", "daily"):
        cli(args.cmd, args.site)
    else:
        host = os.environ.get("HOST", "127.0.0.1")
        if not AUTH_ON and host not in ("127.0.0.1", "localhost", "::1"):
            sys.exit("Refusing to listen on a network address with login disabled. Add at least one e-mail to auth.admins in config.yaml.")
        port = cfg["settings"]["port"]
        print(f"SEO Compass on http://{host}:{port}" + ("  [DEMO DATA]" if args.demo else "") +
              ("  [login required]" if AUTH_ON else "  [login disabled: local use only]"))
        if AUTH_ON and not HTTPS and host not in ("127.0.0.1", "localhost"):
            print("WARNING: auth.base_url is not https; magic links and session cookies would travel unencrypted.")
        app.run(host=host, port=port, debug=False, threaded=True)
