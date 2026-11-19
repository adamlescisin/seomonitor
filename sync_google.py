"""Pulls Search Console + GA4 data via the free Google APIs into SQLite."""
import datetime as dt
from db import log

GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"

def _creds(cfg, scopes):
    from google.oauth2 import service_account
    f = cfg["google"].get("service_account_file")
    return service_account.Credentials.from_service_account_file(f, scopes=scopes)

def _daterange_start(conn, table, site, default_days, lookback=6):
    row = conn.execute(f"SELECT MAX(date) d FROM {table} WHERE site=?", (site,)).fetchone()
    if row and row["d"]:
        return dt.date.fromisoformat(row["d"]) - dt.timedelta(days=lookback)
    return dt.date.today() - dt.timedelta(days=default_days)

# ---------------------------------------------------------------- Search Console
def _gsc_query(svc, prop, start, end, dims):
    rows, start_row = [], 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": dims,
                "rowLimit": 25000, "startRow": start_row, "dataState": "final"}
        resp = svc.searchanalytics().query(siteUrl=prop, body=body).execute()
        batch = resp.get("rows", [])
        rows += batch
        if len(batch) < 25000:
            return rows
        start_row += 25000

def sync_gsc(cfg, conn, site):
    from googleapiclient.discovery import build
    svc = build("searchconsole", "v1", credentials=_creds(cfg, [GSC_SCOPE]), cache_discovery=False)
    prop = site["gsc_property"]
    end = dt.date.today() - dt.timedelta(days=2)       # GSC lags ~2 days
    start = _daterange_start(conn, "gsc_daily", site["id"], cfg["settings"]["gsc_backfill_days"])
    s, e = start.isoformat(), end.isoformat()
    sid = site["id"]
    # totals per day
    for r in _gsc_query(svc, prop, s, e, ["date"]):
        conn.execute("INSERT OR REPLACE INTO gsc_daily VALUES (?,?,?,?,?,?)",
                     (sid, r["keys"][0], r["clicks"], r["impressions"], r["ctr"], r["position"]))
    # country / device
    for dim in ("country", "device"):
        for r in _gsc_query(svc, prop, s, e, ["date", dim]):
            conn.execute("INSERT OR REPLACE INTO gsc_dim VALUES (?,?,?,?,?,?,?)",
                         (sid, r["keys"][0], dim, r["keys"][1], r["clicks"], r["impressions"], r["position"]))
    # query x page
    n = 0
    for r in _gsc_query(svc, prop, s, e, ["date", "query", "page"]):
        conn.execute("INSERT OR REPLACE INTO gsc_qp VALUES (?,?,?,?,?,?,?)",
                     (sid, r["keys"][0], r["keys"][1], r["keys"][2], r["clicks"], r["impressions"], r["position"]))
        n += 1
    conn.commit()
    log(conn, sid, "gsc", True, f"{s}..{e}, {n} query/page rows")
    return n

# ---------------------------------------------------------------- GA4
def _ga_report(client, prop, start, end, dims, metrics):
    from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Dimension, Metric
    out, offset = [], 0
    while True:
        req = RunReportRequest(property=f"properties/{prop}",
                               date_ranges=[DateRange(start_date=start, end_date=end)],
                               dimensions=[Dimension(name=d) for d in dims],
                               metrics=[Metric(name=m) for m in metrics],
                               limit=100000, offset=offset)
        resp = client.run_report(req)
        for row in resp.rows:
            out.append(([d.value for d in row.dimension_values], [float(m.value or 0) for m in row.metric_values]))
        offset += 100000
        if offset >= resp.row_count:
            return out

def sync_ga4(cfg, conn, site):
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    client = BetaAnalyticsDataClient(credentials=_creds(cfg, [GA_SCOPE]))
    prop, sid = site["ga4_property_id"], site["id"]
    end = dt.date.today()
    start = _daterange_start(conn, "ga_daily", sid, 480, lookback=3)
    s, e = start.isoformat(), end.isoformat()
    fmt = lambda d: f"{d[:4]}-{d[4:6]}-{d[6:]}"
    metrics = ["sessions", "totalUsers", "engagedSessions", "keyEvents", "purchaseRevenue"]
    rows = _ga_report(client, prop, s, e,
                      ["date", "sessionDefaultChannelGroup", "sessionSource", "sessionMedium"], metrics)
    for d, m in rows:
        conn.execute("INSERT OR REPLACE INTO ga_daily VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (sid, fmt(d[0]), d[1], d[2], d[3], int(m[0]), int(m[1]), int(m[2]), m[3], m[4]))
    # landing pages (organic + referral channels only keeps it small)
    rows2 = _ga_report(client, prop, s, e,
                       ["date", "landingPagePlusQueryString", "sessionDefaultChannelGroup", "sessionSource"],
                       ["sessions", "engagedSessions", "keyEvents", "purchaseRevenue"])
    for d, m in rows2:
        conn.execute("INSERT OR REPLACE INTO ga_landing VALUES (?,?,?,?,?,?,?,?,?)",
                     (sid, fmt(d[0]), d[1].split("?")[0], d[2], d[3], int(m[0]), int(m[1]), m[2], m[3]))
    conn.commit()
    log(conn, sid, "ga4", True, f"{s}..{e}, {len(rows)} rows")
    return len(rows)

def sync_all(cfg, conn, site):
    res = {}
    for name, fn in (("gsc", sync_gsc), ("ga4", sync_ga4)):
        try:
            res[name] = fn(cfg, conn, site)
        except Exception as ex:   # keep going; surface in UI
            log(conn, site["id"], name, False, repr(ex))
            res[name] = f"error: {ex}"
    return res
