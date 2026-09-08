# Geopolitical News Bot — Render Web Service

## Files (web service version)
- `main_web.py` — Flask app + background scheduler thread, binds to `$PORT`
- `requirements_web.txt` — rename to `requirements.txt` in your repo
- `render_web.yaml` — rename to `render.yaml` if using Render Blueprints

## ⚠️ Before you deploy
The original script had a real Gmail app password and phone number hardcoded.
**Rotate that Gmail app password now** (Google Account → Security → App Passwords) and use the new one below. Never commit real secrets into a git repo.

## Why a web server at all?
Render's **Web Service** type expects your app to listen on `$PORT` and respond to HTTP requests — that's how Render checks it's alive. `main_web.py` runs your news scheduler in a background thread and a tiny Flask app in the foreground just to satisfy that check.

Endpoints:
- `GET /` — status JSON (last run time, result, article count)
- `GET /health` — plain `OK` for Render's health check
- `GET /run-now` — manually trigger a brief immediately

## Deploy steps

1. In your repo, rename:
   - `main_web.py` → keep as-is (referenced by start command)
   - `requirements_web.txt` → `requirements.txt`
   - `render_web.yaml` → `render.yaml` (optional, for Blueprint deploy)

2. In Render:
   - **New → Web Service**
   - Connect your repo
   - Environment: `Python 3`
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn -w 1 -k gthread --threads 4 --timeout 120 main_web:app`
   - Health Check Path: `/health`
   - Plan: the **free tier will spin down after 15 min of no HTTP traffic**, which kills your background scheduler thread too. For a bot that must run unattended on a real interval, use at least the Starter plan (or the earlier Background Worker version, which doesn't have this idle-spindown problem).

3. Add environment variables (**Environment** tab):

   | Key | Example value |
   |---|---|
   | `PHONE` | `919487232929` |
   | `APIKEY` | your CallMeBot API key |
   | `EMAIL_SENDER` | `you@gmail.com` |
   | `EMAIL_PASSWORD` | new Gmail App Password |
   | `EMAIL_RECEIVERS` | `a@x.com,b@y.com,c@z.com` |
   | `MAX_ARTICLES` | `30` (optional) |
   | `INTERVAL_HOURS` | `3` (optional) |
   | `PORT` | set automatically by Render — don't set manually |

4. Deploy. On boot the scheduler thread fires one test brief immediately, then repeats every `INTERVAL_HOURS`. Visit your Render URL to see status JSON, or hit `/run-now` to trigger on demand.

## Free tier caveat
If you're on Render's **free Web Service** tier: it sleeps after 15 minutes of no inbound HTTP traffic, and your background thread sleeps with it — so scheduled sends will silently stop until something pings the URL again. Two ways around this:
- Use a paid plan (no spin-down), or
- Keep it free but use an external uptime pinger (e.g. UptimeRobot hitting `/health` every 10 min) to keep it awake — though this is a workaround, not guaranteed reliable.

If reliability matters more than a public URL, the **Background Worker** version from earlier avoids this problem entirely.
