# Deploying Tallied

The API and its Postgres database run on **a Hetzner server managed with Coolify**. The web app runs
on **Vercel**. Nothing here has been deployed yet: these are the steps, in order. Placeholders look
like `YOUR-DOMAIN`.

```
Browser ──> Vercel (web/, Next.js) ──HTTPS, server-side──> Hetzner server
                                                             Coolify proxy (Traefik, ports 80/443)
                                                               └─> tallied-api container (api/Dockerfile, port 8000)
                                                                     ├─ scheduled task: 24-hour cleanup, every 15 min
                                                                     └─> Postgres container (no public port)
```

The browser only ever talks to Vercel. Vercel's server-side routes call the API at `API_URL`, which is
never sent to the browser. Uploads go up in pieces of at most 3.5 MB, because Vercel refuses request
bodies over 4.5 MB.

## 1. Generate two secrets

Run this twice and keep both values in a password manager:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

- **Secret A**: `RATE_LIMIT_SALT` (API only)
- **Secret B**: `WEB_PROXY_SECRET` (the same value on the API and on Vercel)

## 2. The Hetzner server

1. In Hetzner Cloud, create a server: Ubuntu 24.04, at least 2 vCPU / 4 GB RAM (Coolify's own
   minimum is 2 GB). Pick an EU location and note it for the privacy page (for example Falkenstein,
   Germany). **Add your SSH key when creating it**, so the server never needs a password.
2. Work through **section 9, server hardening**, steps 1 to 3 (SSH keys only, firewall, automatic
   updates) before installing anything else.
3. DNS: add A records pointing at the server's IPv4 address:
   - `api.YOUR-DOMAIN` for the API
   - `coolify.YOUR-DOMAIN` for the Coolify dashboard
4. Install Coolify (as root, from Coolify's install docs):
   ```bash
   curl -fsSL https://cdn.coollabs.io/coolify/install.sh | sudo bash
   ```
5. Open `http://SERVER-IP:8000`, create the admin account straight away (the first visitor gets it), and
   turn on two-factor authentication. Then in **Settings**, set the instance domain to
   `https://coolify.YOUR-DOMAIN` so the dashboard is served over HTTPS through the proxy. After that,
   port 8000 is closed by the firewall (section 9).

## 3. Postgres (a Coolify database, no public port)

1. In your Coolify project: **+ New**, **Database**, **PostgreSQL**. Choose version **16** (the version
   the app is developed and tested against).
2. Leave **Make it publicly available** **off**. The database is then reachable only from containers on
   the same Coolify network, not from the internet.
3. Start it, then copy its **Postgres URL (internal)**. It looks like
   `postgres://postgres:PASSWORD@CONTAINER-NAME:5432/postgres`. That's `DATABASE_URL` for the API (the
   API converts `postgres://` to the driver it needs).
4. Backups: see **section 9, step 4**. Don't turn on Coolify's built-in backups until you've read it.

## 4. The API (a Coolify Dockerfile application)

1. **+ New**, **Application**, and choose this GitHub repository (a GitHub App gives Coolify read access
   to a private repo).
2. **Build Pack**: `Dockerfile`. **Base Directory**: `/api`. **Dockerfile Location**: `/Dockerfile`.
3. **Ports Exposes**: `8000`. Leave **Ports Mappings** empty, so no host port is published and only
   Coolify's proxy can reach the container.
4. **Domains**: `https://api.YOUR-DOMAIN`. Coolify's proxy gets a Let's Encrypt certificate for it.
5. **Start command**: leave it empty. The Dockerfile's default command, `sh scripts/start.sh`, runs the
   database migrations, then starts uvicorn on port 8000 with `--no-proxy-headers` (section 7).
6. **Health check**: leave Coolify's built-in health check **off**. It runs `curl` or `wget` inside the
   container, and the slim Python image has neither, so it would mark a healthy API as unhealthy. Use
   the external uptime check in section 9, step 5 instead.
7. **Environment Variables**: add the API variables from section 7. Make sure none of them is marked
   as a build variable: they're only needed when the container runs, and secrets shouldn't be baked
   into image layers.
8. Make sure the API and the database are on the same Coolify network (both in the same project and
   environment, on the same server). Then **Deploy**.
9. Check it from your laptop:
   ```bash
   curl https://api.YOUR-DOMAIN/health          # {"status":"ok"}
   curl -s -o /dev/null -w "%{http_code}\n" https://api.YOUR-DOMAIN/docs   # 404 in production
   ```
10. Keep this a single container (no scaling to replicas). Upload pieces are held on the container
    that received them until the check starts. Each redeploy also replaces the container. A check that
    was running at that moment is marked failed by the cleanup within 15 minutes, and its upload folder
    is deleted with the old container. Redeploy when the site is quiet.

## 5. The 24-hour cleanup (a Coolify scheduled task)

The API also runs the cleanup every 10 minutes while it's up (`CLEANUP_INTERVAL_MINUTES`). The
scheduled task is the safety net, and an explicit record that deletion happens.

1. On the **tallied-api** application: **Scheduled Tasks**, **+ Add**.
2. **Name**: `cleanup`. **Command**: `python -m app.services.cleanup`. **Frequency**: `*/15 * * * *`.
   **Container name**: leave empty (the app has one container). Timeout: 300 seconds.
3. Save, then click **Execute Now**. Under **Recent executions** the output should end with a line like
   `cleanup.done wiped=0 tombstones=0 stuck=0 temp=0`.

The task runs inside the API container, so it uses the API's own environment variables. It also
removes abandoned upload folders from that container's temp folder. A task is skipped if the API isn't
running, which is why the uptime check (section 9) matters.

## 6. Vercel: the web app

1. **Add New**, **Project**, import this repository, and set **Root Directory** to `web`.
2. **Environment Variables** (Production): add the web variables from section 7. **Don't** prefix them
   with `NEXT_PUBLIC_`, because that would send them to the browser.
3. Deploy, and note the URL (for example `https://tallied.vercel.app`) and any custom domain.
4. Back in Coolify, set the API's `CORS_ORIGINS` to that exact URL (comma-separated if you also use a
   custom domain), then redeploy the API.
5. Region: `web/vercel.json` runs Vercel's functions in London (`lhr1`). If your Hetzner server is in
   Germany, consider `fra1` (Frankfurt), which is closer to it and inside the EU. The privacy page
   doesn't name a Vercel region.

## 7. Every environment variable

Taken from `api/.env.example` and `web/.env.example`.

### Coolify: `tallied-api`

| Variable | Value | Notes |
|---|---|---|
| `ENVIRONMENT` | `production` | Turns off the interactive API docs. The Dockerfile already defaults to it |
| `ANTHROPIC_API_KEY` | your key | Secret. Set a spending limit on the account first |
| `COMPANIES_HOUSE_API_KEY` | your key | Secret. Without it, "Employer is a real company" shows "We couldn't check this" |
| `DATABASE_URL` | the Postgres **internal** URL from section 3 | Never a public URL: the database has no public port |
| `CORS_ORIGINS` | `https://tallied.vercel.app` | Your Vercel URL(s), comma-separated. Nothing else |
| `CLAUDE_MODEL` | `claude-opus-5-5` | |
| `CLAUDE_EFFORT` | `medium` | |
| `AI_DAILY_CALL_CAP` | `200` | Total Claude calls a day across everyone (cost safety net) |
| `MAX_FILES` | `12` | |
| `MAX_FILE_MB` | `10` | |
| `MAX_PAGES` | `30` | |
| `REPORT_TTL_HOURS` | `24` | Keep at 24: the privacy page promises it |
| `CHECKS_PER_IP_PER_DAY` | `3` | Raise temporarily for the smoke test (section 8) |
| `RATE_LIMIT_SALT` | Secret A | Secret. Never change it after launch, or today's counts reset |
| `TRUST_PROXY_HEADERS` | `false` | Keep false. See "IP addresses behind Coolify's proxy" below |
| `WEB_PROXY_SECRET` | Secret B | Secret. Must match Vercel |
| `UPLOAD_TEMP_DIR` | leave unset | The Dockerfile sets `/tmp/tallied-uploads` |
| `CLEANUP_INTERVAL_MINUTES` | `10` | In-API cleanup loop, alongside the scheduled task |

### Vercel: `web` (Production)

| Variable | Value | Notes |
|---|---|---|
| `API_URL` | `https://api.YOUR-DOMAIN` | Server-side only |
| `WEB_PROXY_SECRET` | Secret B | Same as the API. Server-side only |
| `TRUST_FORWARDED_FOR` | leave unset | Only for hosts other than Vercel. On Vercel the visitor's IP comes from `x-real-ip` |

### IP addresses behind Coolify's proxy

Every request reaches the API through Coolify's proxy, which adds `X-Forwarded-For` and similar
headers. The API ignores all of them:

- uvicorn starts with `--no-proxy-headers` (`api/scripts/start.sh`), so it never takes the client
  address from a forwarded header. Without that flag, uvicorn trusts these headers on connections from
  127.0.0.1, and anyone reaching the API through a local proxy could pick their own IP. This was tested
  locally, before and after the fix.
- `TRUST_PROXY_HEADERS=false`, so the app ignores `X-Forwarded-For` too.
- A visitor's IP is used only when the request carries `X-Tallied-Proxy-Secret` equal to
  `WEB_PROXY_SECRET`, which only Vercel's server-side routes have. They send the visitor's IP in
  `X-Tallied-Client-IP`.

Anyone calling the API directly from the internet therefore counts against the proxy's own address:
direct callers share one daily allowance of 3, and normal visitors are unaffected. After deploying,
check it from your laptop. All four requests claim different IPs, and the fourth should get 429:

```bash
for i in 1 2 3 4; do
  curl -s -o /dev/null -w "%{http_code}\n" -H "X-Forwarded-For: 203.0.113.$i" \
       -H "X-Tallied-Client-IP: 203.0.113.$i" -F monthly_rent=1100 -F consent=true \
       -F files=@fixtures/genuine/payslip_2026-09.pdf https://api.YOUR-DOMAIN/checks
done
```

### Reading production data from your laptop (optional)

The database has no public port, so reach it through an SSH tunnel to its container. On the server:

```bash
docker ps --format '{{.Names}}  {{.Image}}' | grep postgres            # find the database container
docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' CONTAINER-NAME
```

Then on your laptop, with that IP address:

```bash
ssh -N -L 5433:CONTAINER-IP:5432 root@SERVER-IP        # leave running
cd api
DATABASE_URL="postgresql://postgres:PASSWORD@127.0.0.1:5433/postgres" uv run python scripts/funnel.py --days 7
```

## 8. Final end-to-end test against the deployed URLs

This uploads all seven synthetic fixtures through the live web app, exactly as a browser would, and
checks every verdict, every evidence image, the privacy page, the security headers and the API
hardening. That's 7 checks from your IP, so:

1. In Coolify, set `CHECKS_PER_IP_PER_DAY` to `10` on `tallied-api` and redeploy (or restart).
2. Run:
   ```bash
   cd api
   uv run python scripts/smoke_test.py --web https://tallied.vercel.app --api https://api.YOUR-DOMAIN
   ```
   Expect `N/N checks passed`, and a table where every fixture's actual verdict matches the expected
   one. "privacy placeholders filled in" fails until you've filled in `web/src/app/privacy/page.tsx`.
3. Set `CHECKS_PER_IP_PER_DAY` back to `3` and redeploy.
4. On your phone, upload one fixture, open the report, tap a flag, and leave feedback.

## 9. Server hardening

Do steps 1 to 3 before installing Coolify, and steps 4 and 5 before real people use Tallied.

### 1. SSH keys only
- [ ] Your key was added when the server was created. Confirm you can log in with it.
- [ ] In `/etc/ssh/sshd_config` (or a file in `/etc/ssh/sshd_config.d/`), set:
  ```
  PasswordAuthentication no
  KbdInteractiveAuthentication no
  PermitRootLogin prohibit-password
  ```
  `prohibit-password` still allows root with a key, which Coolify uses to manage the server. It blocks
  root passwords.
- [ ] `systemctl reload ssh`, then **open a second terminal and check you can still log in** before
  closing the first.
- [ ] Turn on two-factor authentication on your Hetzner account and on the Coolify dashboard.

### 2. Firewall
Use **Hetzner Cloud Firewall** (applied outside the server) as the main firewall. Ports that Docker
publishes skip `ufw` rules on the server itself, so `ufw` alone wouldn't protect Coolify's ports.

- [ ] Inbound allow: TCP 22 (only from your own IP address if it's fixed), TCP 80 and 443, and
      ICMP (optional).
- [ ] Everything else inbound is denied. That includes Coolify's dashboard port **8000** and its
      realtime ports **6001** and **6002**, once the dashboard is on `https://coolify.YOUR-DOMAIN`.
- [ ] Attach the firewall to the server, then check from your laptop: `curl -m 5 http://SERVER-IP:8000`
      should time out, and `https://api.YOUR-DOMAIN/health` should work.

### 3. Automatic security updates
- [ ] `apt update && apt install -y unattended-upgrades && dpkg-reconfigure -plow unattended-upgrades`
- [ ] Decide on reboots. With one server, a kernel update needs a reboot to take effect, and a reboot
      means a minute or two of downtime. Either set `Unattended-Upgrade::Automatic-Reboot "true";` with
      `Unattended-Upgrade::Automatic-Reboot-Time "04:00";` in `/etc/apt/apt.conf.d/50unattended-upgrades`,
      or reboot by hand when `/var/run/reboot-required` exists.
- [ ] Keep Coolify itself updated (its dashboard shows when an update is available).

### 4. Postgres backups, stored off the server, with a tested restore

**Read this first: a full backup conflicts with the 24-hour promise.** Most rows in the database are
short-lived reports and page images, which the privacy page says are deleted after 24 hours. A full
backup copies whatever reports exist at that moment and keeps them for as long as you keep the backup.
What's worth keeping is the feedback, the launch-news emails and the anonymous analytics.

- [ ] **Recommended: back up only what needs keeping.** On the server, a daily root cron job that dumps
      everything except the report data, and copies it off the server:
  ```bash
  # /root/tallied-backup.sh   (cron: 30 3 * * *  /root/tallied-backup.sh)
  set -eu
  DB=$(docker ps --format '{{.Names}}' | grep -m1 POSTGRES-CONTAINER-PREFIX)
  OUT=/root/backups/tallied-$(date +%F).dump
  mkdir -p /root/backups
  docker exec "$DB" pg_dump -U postgres -d postgres -Fc \
      --exclude-table-data=checks --exclude-table-data=page_images > "$OUT"
  rclone copy "$OUT" offsite:tallied-backups/      # an rclone remote you've set up (see below)
  find /root/backups -name 'tallied-*.dump' -mtime +2 -delete
  ```
  The table structure is still backed up; only the rows of `checks` and `page_images` are left out.
  For `offsite`, use S3-compatible storage somewhere other than this server, for example Hetzner
  Object Storage in a different location, or Backblaze B2. Keep the bucket private, and turn on the
  provider's lifecycle rule to delete backups after, say, 30 days.
- [ ] **Or use Coolify's built-in scheduled backups** (database, **Backups**, an S3 destination). They
      back up the whole database, reports included. If you use them, keep retention short and add a
      line to the privacy page saying that encrypted backups containing reports are kept for that
      many days.
- [ ] **Don't turn on Hetzner's server backups or snapshots** for this server. They copy the whole disk,
      including reports and any upload that's being processed, and keep it for up to 7 days.
- [ ] **Test a restore, and repeat it monthly.** Download the latest backup to your laptop and restore it
      into the local Postgres:
  (On Windows the `createdb`, `pg_restore` and `psql` commands are in `C:\Program Files\PostgreSQL\16\bin`.)
  ```bash
  cd api
  uv run python scripts/local_db.py start
  createdb -h 127.0.0.1 -p 55432 -U tallied restore_test
  pg_restore -h 127.0.0.1 -p 55432 -U tallied -d restore_test --no-owner --no-privileges tallied-YYYY-MM-DD.dump
  psql -h 127.0.0.1 -p 55432 -U tallied -d restore_test \
       -c "select count(*) from feedback" -c "select count(*) from events" -c "select count(*) from checks"
  dropdb -h 127.0.0.1 -p 55432 -U tallied restore_test
  ```
  Expect real counts for `feedback` and `events`, and 0 for `checks`. Write down the date of each
  successful test.

### 5. Uptime check on the health endpoint
- [ ] An external monitor (for example UptimeRobot or Better Stack) on `https://api.YOUR-DOMAIN/health`
      every 1 to 5 minutes. Expect HTTP 200 with body `{"status":"ok"}`, and send alerts to your email
      or phone.
- [ ] A second monitor on the web app, `https://tallied.vercel.app/`.
- [ ] Weekly, glance at the cleanup task's **Recent executions** in Coolify, and at disk space
      (`df -h`); Docker images build up over many deploys (`docker image prune` cleans them).

## 10. Before real people use it

- [ ] Register with the ICO as a data controller (https://ico.org.uk/for-organisations/data-protection-fee/).
      Then fill in `web/src/app/privacy/page.tsx`: your name or company, contact email, ICO number, and the
      Hetzner location.
- [ ] Check the 2026/27 tax and NI figures in `api/app/checks/tax_config.py` against GOV.UK
      ("Rates and thresholds for employers 2026 to 2027")
- [ ] Get a Companies House API key (https://developer.company-information.service.gov.uk/) and set it
- [ ] Set a monthly spending limit on your Claude API account, and leave `AI_DAILY_CALL_CAP` at 200 or lower
- [ ] Run the live AI tests once with a real key: `uv run pytest -m live_ai -s`
- [ ] Finish section 9 (hardening, backups with a tested restore, uptime checks)
- [ ] Test with your own real statements first, then 5 to 10 friendly agents or landlords
- [ ] Decide what you'll measure, and look at it weekly with `scripts/funnel.py` (section 7):
  - checks finished as a share of checks started
  - thumbs up vs down (`Was it useful?` line)
  - emails left
  - evidence opened as a share of reports viewed
