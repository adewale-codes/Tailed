# Deploying Tallied

The API and its Postgres database run on **Heroku, in the Europe region**. The web app runs on
**Vercel**. Nothing here has been deployed yet: these are the steps, in order. Placeholders look like
`YOUR-APP`.

```
Browser ──> Vercel (web/, Next.js) ──HTTPS, server-side──> Heroku router (eu)
                                                              └─> 1 Basic web dyno: api/Dockerfile, listens on $PORT
                                                                    ├─ release phase: alembic upgrade head
                                                                    ├─ in-app cleanup loop every 10 min
                                                                    └─> Heroku Postgres Essential-0 (eu)
                                       Heroku Scheduler ──every 10 min──> one-off dyno: python -m app.services.cleanup
```

The browser only ever talks to Vercel. Vercel's server-side routes call the API at `API_URL`, which is
never sent to the browser. Uploads go up in pieces of at most 3.5 MB, because Vercel refuses request
bodies over 4.5 MB. The API joins the pieces in its temp folder, so **it must run as exactly one dyno**.

> **⚠ Never run more than one API dyno, and keep Heroku Preboot off.**
>
> Three parts of the API assume there is only ever one copy of it running, and no two copies at
> the same moment:
>
> 1. **Upload pieces.** Each piece of an upload is saved to the temp folder of the dyno that
>    received it. With two dynos, Heroku's router would send pieces of the same upload to different
>    dynos, and the check would fail with "This upload has expired".
> 2. **The one-at-a-time queue.** `MAX_CONCURRENT_CHECKS=1` keeps memory under 512 MB by making
>    checks wait their turn in a queue held in that dyno's memory, not in the database. Nothing
>    coordinates queues across dynos: with two, each would run its own queue, and the database
>    couldn't tell which dyno a waiting check belongs to (see 3).
> 3. **The restart fix.** When the API starts, it marks every check that is still processing as
>    failed and gives the visitor's check back, because the previous copy must be gone. If an old and
>    a new copy overlap, the new one would fail checks the old one is still finishing.
>
> **Preboot** is the Heroku feature that starts the new dynos and sends them traffic *before* the old
> ones stop, which is exactly that overlap. According to Heroku's docs, "Preboot is only available on
> the Common Runtime to apps that are using `standard` and `performance` dynos", so **it isn't
> available on Basic dynos** at all. On Basic, Heroku stops the old dyno before starting the new one.
> Basic dynos also can't autoscale. So on Basic, the only ways to break this are scaling by hand
> (`heroku ps:scale web=2`), or moving to Standard or Performance dynos and then turning on Preboot
> or autoscaling.
>
> If you ever move off Basic: keep `web=1`, don't enable autoscaling, and check that Preboot is off with
> `heroku features -a YOUR-APP` (it must show `[ ] preboot`; turn it off with
> `heroku features:disable preboot -a YOUR-APP`). Source:
> https://devcenter.heroku.com/articles/preboot

What you'll pay (Heroku's list prices; check the dashboard): Basic dyno $7/month, Postgres
Essential-0 about $5/month, Scheduler free, but its one-off dynos are billed at the Basic rate per
second they run (144 short runs a day, likely well under $1/month; not measured).

## 1. Generate two secrets

Run this twice and keep both values in a password manager:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

- **Secret A**: `RATE_LIMIT_SALT` (API only)
- **Secret B**: `WEB_PROXY_SECRET` (the same value on the API and on Vercel)

## 2. Heroku account and CLI

1. Create a Heroku account and **turn on multi-factor authentication** (Account settings).
2. Install the Heroku CLI (https://devcenter.heroku.com/articles/heroku-cli), then `heroku login`.
   Git must be installed too (you deploy with `git push`).

## 3. The app, in the Europe region, on the container stack

From the repository root:

```bash
heroku create YOUR-APP --region eu --stack container
heroku git:remote -a YOUR-APP          # adds the "heroku" git remote, if create didn't
heroku info -a YOUR-APP                # check "Region: eu" and "Stack: container"; note the "Web URL"
```

- `--region eu` can only be chosen when the app is created. Apps are created in `us` if you forget it;
  if that happens, delete the app and create it again.
- The web URL looks like `https://YOUR-APP-1a2b3c4d5e6f.herokuapp.com`. That's `API_URL` for Vercel.
- `heroku.yml` (repository root) tells Heroku to build `api/Dockerfile`, with `api/` as the build
  context, so `web/` and `fixtures/` never go into the image.
- Confirm where `eu` physically is before you publish the privacy page: `heroku regions --json`
  (look for `"name": "eu"`). The privacy page says Ireland.

## 4. Postgres: Heroku Postgres Essential-0, in the same region

```bash
heroku addons:create heroku-postgresql:essential-0 -a YOUR-APP -- --version=16
heroku pg:wait -a YOUR-APP
heroku pg:info -a YOUR-APP            # check Plan: Essential 0, PG Version: 16, and the region
```

- Version 16 is what the app is developed and tested against. Essential supports 16, 17 and 18.
- Add-ons are created in the app's region, and Essential databases only exist in `us-east-1` or
  `eu-west-1`. `heroku pg:info` should show the EU one.
- This sets the `DATABASE_URL` config var for you (`postgres://...`). The API converts it to the driver
  it needs, and the connection is encrypted (Heroku requires SSL; psycopg negotiates it).
- **Don't schedule PGBackups** (`heroku pg:backups:schedule`). Heroku stores those backups **in the
  US** and keeps 7 daily plus 1 weekly, and they would contain reports. See section 10.

## 5. Config vars

Config vars are only available when the container runs, never at build time, so no secret ends up
in the image. Every change restarts the dyno.

```bash
heroku config:set -a YOUR-APP \
  ENVIRONMENT=production \
  ANTHROPIC_API_KEY=... \
  COMPANIES_HOUSE_API_KEY=... \
  RATE_LIMIT_SALT="Secret A" \
  WEB_PROXY_SECRET="Secret B" \
  CORS_ORIGINS=https://tallied.vercel.app \
  CLAUDE_MODEL=claude-opus-5-5 CLAUDE_EFFORT=medium AI_DAILY_CALL_CAP=200 \
  MAX_FILES=12 MAX_FILE_MB=10 MAX_PAGES=30 \
  REPORT_TTL_HOURS=24 CHECKS_PER_IP_PER_DAY=3 TRUST_PROXY_HEADERS=false \
  CLEANUP_INTERVAL_MINUTES=10 MAX_CONCURRENT_CHECKS=1
```

Put the real secrets in by typing them into this command (or the dashboard's **Settings → Reveal
Config Vars**). Don't save them in a script or commit them. Section 7 explains every variable.

## 6. Deploy the API: one Basic dyno

```bash
git push heroku main
```

Heroku builds the image from `api/Dockerfile`, then runs the **release phase** (`alembic upgrade head`)
in a one-off dyno. If a migration fails, the release is abandoned and the previous one keeps running.
Then it starts the web dyno with the Dockerfile's command (`sh scripts/start.sh`), which listens on
`$PORT` and starts uvicorn with `--no-proxy-headers`.

Then pin the dyno type and count. **Basic, exactly one**, never Eco (Eco dynos sleep) and never more
than one:

```bash
heroku ps:type web=basic -a YOUR-APP
heroku ps:scale web=1 -a YOUR-APP
heroku ps -a YOUR-APP                  # expect exactly one line: web.1 ... (Basic)
```

Why only one: upload pieces live in that dyno's temp folder until the check starts, and checks are
queued in that dyno's memory. A second dyno would get pieces of the same upload. (Basic dynos can't
autoscale, so this stays true unless someone scales by hand.)

Check it from your laptop:

```bash
curl https://YOUR-APP-xxxx.herokuapp.com/health                                   # {"status":"ok"}
curl -s -o /dev/null -w "%{http_code}\n" https://YOUR-APP-xxxx.herokuapp.com/docs  # 404 in production
heroku logs --tail -a YOUR-APP        # Ctrl+C to stop; expect "cleanup.done ..." shortly after boot
```

Redeploying restarts the dyno. Heroku stops the old dyno before starting the new one (preboot isn't
available on Basic), so expect a few seconds of errors. A check running at that moment may be lost
(see "Daily restarts" in section 9). Deploy when the site is quiet.

## 7. Every environment variable

Taken from `api/.env.example` and `web/.env.example`.

### Heroku: `YOUR-APP` (config vars)

| Variable | Value | Notes |
|---|---|---|
| `DATABASE_URL` | set by the Postgres add-on | Don't set or change it yourself. Heroku may rotate it, which restarts the dyno |
| `PORT` | set by Heroku | Don't set it. `start.sh` listens on it (8000 when run locally) |
| `ENVIRONMENT` | `production` | Turns off the interactive API docs. The Dockerfile already defaults to it |
| `ANTHROPIC_API_KEY` | your key | Secret. Set a spending limit on the account first |
| `COMPANIES_HOUSE_API_KEY` | your key | Secret. Without it, "Employer is a real company" shows "We couldn't check this" |
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
| `TRUST_PROXY_HEADERS` | `false` | Keep false. See "IP addresses behind Heroku's router" below |
| `WEB_PROXY_SECRET` | Secret B | Secret. Must match Vercel |
| `UPLOAD_TEMP_DIR` | leave unset | The Dockerfile sets `/tmp/tallied-uploads`, on the dyno's ephemeral disk |
| `CLEANUP_INTERVAL_MINUTES` | `10` | In-app cleanup loop. It's the only thing that clears the web dyno's temp folder |
| `MAX_CONCURRENT_CHECKS` | `1` | Keep 1 on a Basic dyno (512 MB). See "Memory" in section 9 |

### Vercel: `web` (Production)

| Variable | Value | Notes |
|---|---|---|
| `API_URL` | `https://YOUR-APP-xxxx.herokuapp.com` | The web URL from `heroku info`. Server-side only |
| `WEB_PROXY_SECRET` | Secret B | Same as the API. Server-side only |
| `TRUST_FORWARDED_FOR` | leave unset | Only for hosts other than Vercel. On Vercel the visitor's IP comes from `x-real-ip` |

### IP addresses behind Heroku's router

Every request reaches the dyno through Heroku's router, which adds `X-Forwarded-For`,
`X-Forwarded-Proto` and similar headers. The API ignores all of them:

- uvicorn starts with `--no-proxy-headers` (`api/scripts/start.sh`), so it never takes the client
  address from a forwarded header. A test checks this flag stays.
- `TRUST_PROXY_HEADERS=false`, so the app ignores `X-Forwarded-For` too.
- A visitor's IP is used only when the request carries `X-Tallied-Proxy-Secret` equal to
  `WEB_PROXY_SECRET`, which only Vercel's server-side routes have. They send the visitor's IP in
  `X-Tallied-Client-IP`.

Anyone calling the API directly from the internet therefore counts against the router's address
(which varies), never against an IP they choose. After deploying, check it from your laptop. All four
requests claim different IPs. Expect the fourth to get 429, as long as Heroku's router reaches the
dyno from the same address each time (not verified; if you see four 202s, rerun it with more requests):

```bash
for i in 1 2 3 4; do
  curl -s -o /dev/null -w "%{http_code}\n" -H "X-Forwarded-For: 203.0.113.$i" \
       -H "X-Tallied-Client-IP: 203.0.113.$i" -F monthly_rent=1100 -F consent=true \
       -F files=@fixtures/genuine/payslip_2026-09.pdf https://YOUR-APP-xxxx.herokuapp.com/checks
done
```

### Reading production data from your laptop (optional)

```bash
heroku pg:psql -a YOUR-APP                          # needs a local psql
cd api
DATABASE_URL="$(heroku config:get DATABASE_URL -a YOUR-APP)?sslmode=require" uv run python scripts/funnel.py --days 7
```

## 8. Vercel: the web app

1. **Add New**, **Project**, import this repository, and set **Root Directory** to `web`.
2. **Environment Variables** (Production): add the web variables from section 7. **Don't** prefix them
   with `NEXT_PUBLIC_`, because that would send them to the browser.
3. Deploy, and note the URL (for example `https://tallied.vercel.app`) and any custom domain.
4. Set the API's `CORS_ORIGINS` to that exact URL (comma-separated if you also use a custom domain):
   `heroku config:set CORS_ORIGINS=https://tallied.vercel.app -a YOUR-APP` (this restarts the dyno).
5. Region: `web/vercel.json` runs Vercel's functions in Dublin (`dub1`), next to Heroku's `eu` region
   in Ireland and inside the EU. The privacy page doesn't name a Vercel region.

## 9. The 24-hour cleanup, and Heroku's limits

### Heroku Scheduler, every 10 minutes (its most frequent interval)

Heroku Scheduler offers **every 10 minutes, every hour, or every day**. 10 minutes is the most
frequent. Heroku says runs are "expected but not guaranteed" and can occasionally be skipped or run
twice. The cleanup is safe to run twice, and the API also runs it itself every 10 minutes, so the
Scheduler job is the safety net (and works while the web dyno is down).

```bash
heroku addons:create scheduler:standard -a YOUR-APP
heroku addons:open scheduler -a YOUR-APP
```

In the Scheduler dashboard, **Add Job**:
- **Schedule**: Every 10 minutes
- **Run Command**: `python -m app.services.cleanup`
- **Dyno size**: Basic

Test it once by hand. The output should end with a line like
`cleanup.done wiped=0 tombstones=0 stuck=0 refunded=0 temp=0`:

```bash
heroku run python -m app.services.cleanup -a YOUR-APP
```

Scheduler jobs run in a one-off dyno built from the `web` image, with the same config vars. That
dyno has its own empty disk, so its `temp=` count is always 0. Abandoned uploads on the web dyno are
cleared by the in-app loop, and by every restart.

### 30-second request timeout

Heroku's router drops a request if the app hasn't started answering within 30 seconds. The clock
starts **after** the whole request body has reached the dyno, and Heroku doesn't stop the work.
None of the API's requests do slow work while the caller waits:

| Request | Measured locally (512 MB container) |
|---|---|
| `POST /uploads/{id}/chunks`, a 3.5 MB piece | 0.08 s |
| `POST /checks` (starts the check, returns 202) | 0.03 to 0.07 s; 0.2 to 0.6 s for a 12-file direct upload |
| `GET /checks/{id}/progress`, `/checks/{id}`, page images | database reads |

Reading, rendering, the seven checks and the Claude calls all run in the background after `202`.
They aren't affected by the router timeout. A maximum-size check took 55 s in the background.

### Ephemeral disk

Each dyno has its own disk, wiped whenever it restarts. The API only writes uploads, in
`/tmp/tallied-uploads`, and deletes them when processing ends. Reports and page images live in
Postgres, so nothing that must survive a restart is on disk. Heroku runs the container as a random
non-root user, **not** the Dockerfile's `USER`. So the upload folder is created at runtime (mode 700)
by whichever user the app runs as. (The old Dockerfile created it at build time, owned by uid 10001.
Tested with a random uid, every upload failed with `PermissionError`.)

### Daily restarts

Heroku restarts every dyno at least once every 24 hours (plus up to 216 random minutes), and on every
deploy, config change or add-on change. It sends `SIGTERM`, waits 30 seconds, then sends `SIGKILL`.
Tested locally with the same 30 seconds:

- A check that finishes within those 30 seconds completes normally. uvicorn waits for it, then exits.
- **A check still running after 30 seconds is killed** (tested with a 336-page check). When the new
  dyno starts, the API marks every check that was still processing as failed straight away, with
  "Something interrupted this check. Please try again. This one hasn't counted towards today's limit.",
  and gives the visitor's daily check back. The visitor sees that within a few seconds of the
  restart, as soon as their page polls again. The uploaded files went with the old dyno's disk.
  - This relies on there being **one** API instance, which never overlaps with the next one (Basic
    dynos have no preboot). With two instances it would fail the other one's live checks.
  - To give the check back, the check row holds the visitor's salted IP hash (the same one the daily
    limit already uses) **only while it's processing**. It's cleared the moment the check ends.
- An upload in progress during the restart gets "This upload has expired. Please choose your files
  again."
- Typical checks take 1 to 3 seconds without Claude. The ones at risk are very large uploads and
  photos or scans read by Claude.

### Memory (Basic dyno: 512 MB)

Measured with the production image in a Linux container limited to 512 MB with no swap, running as a
random uid with `PORT` set, on synthetic documents. Peak is the container's `memory.peak` (memory plus
page cache, which is close to what Heroku counts for R14):

| What was checked | Pages | Peak | Time |
|---|---|---|---|
| Idle, after boot | | 142 MB | |
| Photo fixture (largest fixture file), no Claude key, so it stops at "AI unavailable" | 1 | 193 MB | 0.6 s |
| `multiple_problems` fixture set (5 files) | 8 | 176 MB | 1.5 s |
| 6-month statement (312 transactions) + 2 payslips | 9 | 193 MB | 2 s |
| 3 of those at once | 27 | 196 MB | 3 s |
| One 27-month statement (largest allowed per file: 28 pages) | 28 | 327 MB | 5.5 s |
| Largest check the API accepts: 12 of those | 336 | 426 MB | 55 s |
| 2 largest checks at once, **before** `MAX_CONCURRENT_CHECKS` | 672 | **killed (out of memory)** | |
| 2 largest checks at once, **with** `MAX_CONCURRENT_CHECKS=1` | 672 | 418 MB | 70 s |

Those figures were measured while every page was still being rendered. Now only pages with a finding
are rendered, at the end, so peaks are the same or lower: the largest clean check peaked at 369 MB,
and the largest flagged one at 392 MB (see "Database size").

So it fits, with one check processed at a time: the worst case peaks at about 430 MB of 512 MB. Over
512 MB, Heroku doesn't kill the dyno straight away. It swaps (error R14, slow), then kills it if usage
goes far over (R15). Not measured: a photo or scan read by Claude (needs a real key). It adds the image
and the API response in memory, which should be small next to the page rendering above.

### Database size (Essential-0: 1 GB)

Page images are stored in Postgres for 24 hours, and **only for pages with a finding**: the pages the
evidence view can open, as 150 DPI WebP (quality 75). Clean pages are never rendered. Measured with the
same harness and synthetic documents, page images stored per check:

| Check | Before: every page, 110 DPI PNG | Now: finding pages only, 150 DPI WebP |
|---|---|---|
| `genuine` fixture (5 files, nothing flagged) | 8 pages, 1.14 MB | 0 pages, 0 |
| `edited_balance` fixture (1 flag) | 8 pages, 1.14 MB | 1 page, 133 KB |
| `multiple_problems` fixture (2 flags) | 8 pages, 1.15 MB | 3 pages, 236 KB |
| 6-month statement + 2 payslips (clean) | 9 pages, 1.70 MB | 0 pages, 0 |
| Largest accepted check, clean (12 × 28 pages) | 336 pages, 79.5 MB | 0 pages, 0 |
| Largest accepted check, flagged (10 × 28-page statements + 2 payslips in another name) | 282 pages, 66.4 MB | 2 pages, 102 KB |

A stored page is 50 to 135 KB. The theoretical worst case is a check with a finding on every one of
its up to 360 pages (12 files × 30 pages): about 48 MB. Real documents produce a handful of finding
pages, so 1 GB now holds thousands of checks within 24 hours. Heroku's rules for Essential databases,
for reference:

- 90% of 1 GB: warning email. Over 1 GB: 7 days to fix it.
- **Over 2 GB at any moment: `INSERT` and `UPDATE` are revoked immediately**, and every new check and
  rate-limit count fails until you fix it.
- Deleting rows doesn't shrink the size Heroku measures. Postgres reuses the space for new rows, but
  only `VACUUM FULL` gives it back.

Only a deliberately built document with a finding on every page gets near the worst case: about 42 of
those within 24 hours (14 IP addresses' daily allowance) would pass 2 GB. Keep an eye on it with
`heroku pg:info -a YOUR-APP` (`Data Size`), and act on the 90% email. If it ever becomes a problem,
upgrade to Essential-1 (10 GB) or lower `MAX_PAGES`.

### Database connections

Essential-0 allows 20 connections. The web dyno uses up to 15 (SQLAlchemy's default pool of 5 plus 10
overflow), and each Scheduler or release dyno uses 1.

## 10. Backups and the 24-hour promise

**What Heroku keeps automatically.** Every Heroku Postgres database, Essential included, has
"continuous protection" for disaster recovery: a physical snapshot "at least every 24 hours" plus the
write-ahead log of every change, pushed to storage in the same region as the database. These copies
contain whatever reports existed at the time. Heroku's docs **don't say how long they're kept**
("we delete snapshots when they expire or based on circumstances requiring Heroku's discretion"), and
on Essential you can't use them yourself (no rollback, fork or follower). The privacy page says this
honestly. **Ask Heroku support for the retention period for Essential databases**, and if they give
you a number, put it on the privacy page.

**What Heroku doesn't do unless you ask: PGBackups.** Daily logical backups are free but off until you
run `heroku pg:backups:schedule`. Don't turn them on for this app. They're stored **in the US**, kept
for 7 days (plus 1 weekly), and would contain reports, which the privacy page doesn't allow for.

**Backing up what's worth keeping** (the feedback, launch-news emails and anonymous analytics) without
reports. Run it from your laptop, store the file somewhere private, delete old copies after about 30
days, and don't add reports to it:

```bash
pg_dump "$(heroku config:get DATABASE_URL -a YOUR-APP)?sslmode=require" -Fc --no-owner \
    --exclude-table-data=checks --exclude-table-data=page_images -f tallied-$(date +%F).dump
```

The table structure is still backed up; only the rows of `checks` and `page_images` are left out. Use
a `pg_dump` at least as new as the server (16). **Test a restore, and repeat it monthly.** Restore
into the local Postgres:

```bash
cd api
uv run python scripts/local_db.py start
createdb -h 127.0.0.1 -p 55432 -U tallied restore_test
pg_restore -h 127.0.0.1 -p 55432 -U tallied -d restore_test --no-owner --no-privileges tallied-YYYY-MM-DD.dump
psql -h 127.0.0.1 -p 55432 -U tallied -d restore_test \
     -c "select count(*) from feedback" -c "select count(*) from events" -c "select count(*) from checks"
dropdb -h 127.0.0.1 -p 55432 -U tallied restore_test
```

(On Windows these commands are in `C:\Program Files\PostgreSQL\<version>\bin`.) Expect real counts
for `feedback` and `events`, and 0 for `checks`. Write down the date of each successful test.

## 11. Final end-to-end test against the deployed URLs

This uploads all seven synthetic fixtures through the live web app, exactly as a browser would, and
checks every verdict, every evidence image, the privacy page, the security headers and the API
hardening. That's 7 checks from your IP, so:

1. `heroku config:set CHECKS_PER_IP_PER_DAY=10 -a YOUR-APP`
2. Run:
   ```bash
   cd api
   uv run python scripts/smoke_test.py --web https://tallied.vercel.app --api https://YOUR-APP-xxxx.herokuapp.com
   ```
   Expect `N/N checks passed`, and a table where every fixture's actual verdict matches the expected
   one. "privacy placeholders filled in" fails until you've filled in `web/src/app/privacy/page.tsx`.
3. `heroku config:set CHECKS_PER_IP_PER_DAY=3 -a YOUR-APP`
4. On your phone, upload one fixture, open the report, tap a flag, and leave feedback.

## 12. Monitoring

- [ ] An external uptime monitor (for example UptimeRobot or Better Stack) on
      `https://YOUR-APP-xxxx.herokuapp.com/health` every 1 to 5 minutes. Expect HTTP 200 with body
      `{"status":"ok"}`, and send alerts to your email or phone.
- [ ] A second monitor on the web app, `https://tallied.vercel.app/`.
- [ ] Weekly: `heroku pg:info -a YOUR-APP` (Data Size, see section 9), `heroku ps -a YOUR-APP` (still
      exactly one Basic web dyno), and `heroku logs -a YOUR-APP --source app | grep cleanup.done`.
      Heroku only keeps the last 1,500 log lines, so add a log drain if you want history.
- [ ] Watch for `Error R14` (memory) and `H12` (timeout) in `heroku logs`.
- Logs contain check IDs, timings and error types only. Heroku's router log lines include the request
  path (with the check ID) and the caller's address, which is Vercel's, never the visitor's.

## 13. Before real people use it

- [ ] Register with the ICO as a data controller (https://ico.org.uk/for-organisations/data-protection-fee/).
      Then fill in `web/src/app/privacy/page.tsx`: your name or company, contact email, ICO number and
      the "Last updated" date. Confirm the `eu` region is in Ireland (section 3) and ask Heroku how long
      continuous-protection copies are kept (section 10).
- [ ] Glance at `heroku pg:info` (Data Size) after the first week (section 9, "Database size").
- [ ] Check the 2026/27 tax and NI figures in `api/app/checks/tax_config.py` against GOV.UK
      ("Rates and thresholds for employers 2026 to 2027")
- [ ] Get a Companies House API key (https://developer.company-information.service.gov.uk/) and set it
- [ ] Set a monthly spending limit on your Claude API account, and leave `AI_DAILY_CALL_CAP` at 200 or lower
- [ ] Run the live AI tests once with a real key: `uv run pytest -m live_ai -s`
- [ ] Multi-factor authentication on Heroku, Vercel and GitHub
- [ ] Monitoring (section 12) and a tested restore of the feedback backup (section 10)
- [ ] Test with your own real statements first, then 5 to 10 friendly agents or landlords
- [ ] Decide what you'll measure, and look at it weekly with `scripts/funnel.py` (section 7):
  - checks finished as a share of checks started
  - thumbs up vs down (`Was it useful?` line)
  - emails left
  - evidence opened as a share of reports viewed
