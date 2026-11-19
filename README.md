# SEO Compass

Self-hosted SEO + AI-search (GEO) dashboard for your own sites. Runs on your Mac Mini, free, with history kept in a local SQLite file. The UI is in Czech by default (CZ/EN switch in the header), sites are chosen from a dropdown, and you can share dashboards with clients through passwordless email login.

| Ahrefs-style area | What SEO Compass does | Data source |
|---|---|---|
| Site performance / trends | Clicks, impressions, CTR, position, device, country, 7-day trends, period-over-period | Search Console API |
| Traffic and revenue | Sessions, channels, organic vs total, revenue, organic revenue per landing page | GA4 Data API |
| Keywords | Full keyword table with change vs previous period | Search Console |
| Keyword opportunities | Striking distance (pos 4-20), low-CTR, cannibalisation, new/rising/lost keywords, zero-click pages | Search Console |
| Keyword ideas | Google Autocomplete expansion of your top keywords; flags ideas you don't rank for yet | Google Suggest |
| Site Audit | 25+ checks: broken links, redirect chains, titles, meta, H1, canonicals, noindex-in-sitemap, orphans, thin content, slow pages, duplicates, schema; health score and change vs last crawl | Own crawler |
| GEO / AI visibility | AI-assistant referral traffic (ChatGPT, Perplexity, Gemini, Copilot, Claude...), landing pages they send people to, AI-bot access in robots.txt, llms.txt, schema coverage | GA4 + crawler |

**What it cannot do** (these need Ahrefs' proprietary index): backlink profiles, competitor keyword data, search volumes and keyword difficulty for keywords you don't already rank for. For those, use Ahrefs Webmaster Tools (free, verified own sites) alongside this.

## Try it right now (no Google account needed)

```bash
./setup.sh
./run.sh --demo        # open http://localhost:8765
```

## Connect your real data (about 15 minutes, once)

### 1. Create a Google service account (free)
1. Go to <https://console.cloud.google.com>, create a project (e.g. "seo-compass").
2. **APIs & Services > Library**: enable **Google Search Console API** and **Google Analytics Data API**.
3. **IAM & Admin > Service Accounts > Create**. Name it anything. Skip roles.
4. Open the account > **Keys > Add key > JSON**. Save the file as `service-account.json` in this folder.
5. Copy the service account's email (looks like `seo-compass@project.iam.gserviceaccount.com`).

### 2. Give it read access
- **Search Console**: for each property, Settings > Users and permissions > Add user > paste the email > **Restricted** is enough.
- **GA4**: Admin > Property access management > add the email as **Viewer**.

### 3. Fill in `config.yaml`
- `gsc_property`: `sc-domain:example.cz` for Domain properties, or the full URL (`https://example.cz/`) for URL-prefix properties. It must match exactly what Search Console shows.
- `ga4_property_id`: GA4 > Admin > Property details (a number, not the `G-XXXX` id).
- `sitemaps`: your sitemap index URL(s). WooCommerce with Yoast or Rank Math is usually `/sitemap_index.xml`.
- Add more sites by copying a block.

### 4. First run
```bash
./run.sh sync          # pulls up to 16 months of Search Console + GA4 history
./run.sh crawl         # site audit
./run.sh ideas         # keyword ideas
./run.sh               # dashboard at http://localhost:8765
```
The buttons in the top bar do the same thing from the browser.

## Keep it running on the Mac Mini

Edit the `/PATH/TO/` in both files in `launchd/`, then:
```bash
cp launchd/*.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/cz.akpro.seocompass.server.plist   # dashboard, always on
launchctl load ~/Library/LaunchAgents/cz.akpro.seocompass.daily.plist    # 06:15 sync; Mondays also crawl + ideas
launchctl load ~/Library/LaunchAgents/cz.akpro.seocompass.evening.plist  # 18:20 sync (second refresh of the day)
```
To open it from other devices, start with `HOST=0.0.0.0`. The app **refuses to bind to anything but localhost unless login is enabled** (see below).

## Sharing with clients (login by email link)

Without any setup the app is open on localhost only, for you. To give clients access:

1. In `config.yaml` set your own email(s) as administrators (copy the `auth:` and `smtp:` blocks from `config.example.yaml`):
   ```yaml
   auth:
     admins: ["you@example.cz"]
     base_url: "https://seo.example.cz"   # address clients will open
     trust_proxy: true                    # only when behind a reverse proxy / tunnel
   smtp:
     host: smtp.hostinger.com
     port: 465
     security: ssl
     username: "seo@example.cz"
     password: "..."
     from: "SEO Compass <seo@example.cz>"
   ```
   Restart the app. Admins see everything plus the **Nastavení / Settings** tab.
2. In **Settings > Add user**, enter the client's email and choose: which **sites** they see, which **sections** (Overview, Keywords, Pages, Opportunities, Audit, AI/GEO, Ideas), and whether they may see **revenue** (when off, revenue figures are never sent to their browser at all). Tick "send invitation" and they get a one-time sign-in link.
3. Clients open the address, type their email and get a link by mail (valid 15 minutes, usable once; session lasts 30 days). You can edit access, resend a link, sign someone out everywhere, block or delete a user at any time, and the Settings tab keeps an access log.

Without SMTP the link is printed in the server log and shown to you in Settings to copy and send yourself.

**Making it reachable by clients:** put it behind HTTPS, for example a Cloudflare Tunnel from the Mac Mini to `seo.example.cz` (no open router ports), and set `base_url` to that https address. Settings warns you if it is not https.

Security notes: only hashes of tokens and sessions are stored; login responses never reveal whether an email exists; requests are rate limited; clients can only call the endpoints of sections they were granted, and server-side checks (not just hidden tabs) enforce sites, sections and revenue. Admins can only be changed in `config.yaml`.

## Updating from the previous version

Overwrite all files except `config.yaml` and `data/` (your database). Add the new `auth:` and `smtp:` blocks to your `config.yaml` (optional; without `auth.admins` it behaves as before). The new `users`, `sessions` and log tables are created automatically.

## Adding a site

Add it to `sites:` in `config.yaml`, give the service account access in its Search Console and GA4 properties, then use **Aktualizace dat → Restartovat aplikaci a načíst konfiguraci** in the dashboard. The app re-reads the config, the page reloads, and the new site appears in the dropdown. Then run **Synchronizovat data** (and the audit) for it. The restart is refused while a sync or audit is running.

## Reading the numbers

- **Position / clicks** come straight from Search Console and match its UI. Data lags about 2 days.
- **Striking-distance "gain"** = impressions x CTR typical for position 3 minus current clicks. It is an estimate for ranking, not a forecast.
- **AI referral sessions** only count visits that arrive with an AI assistant as referrer. Assistants often mention you without sending a click, and some strip the referrer, so this is a floor. If you have server access logs, grepping for `GPTBot`, `OAI-SearchBot`, `PerplexityBot`, `ClaudeBot` shows crawler activity (separate from visits).
- **Keyword ideas** have no volume. Validate demand with Google Trends or Keyword Planner before writing content.
- The crawler skips cart, checkout, account and filter URLs, and caps at `crawl_max_pages` (default 600). Raise it for larger shops.

## Files

`app.py` server + CLI · `sync_google.py` Google APIs · `crawler.py` audit + AI-readiness · `insights.py` all analysis · `keywords.py` ideas · `demo.py` fake data · `auth.py` login, sessions, permissions · `templates/index.html` dashboard + Settings (Chart.js bundled, works offline) · `templates/auth.html` sign-in page
