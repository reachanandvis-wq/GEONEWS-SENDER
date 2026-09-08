# ============================================================
# Geopolitical News Bot → Email only
# Render WEB SERVICE version
# (binds to $PORT so Render's health check passes; scheduler
#  runs in a background thread)
# ============================================================

import os
import sys
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Force line-buffered stdout so print() statements from the background
# thread show up in Render's logs immediately instead of sitting in a
# buffer that never gets flushed (the process never exits on its own).
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

import requests
import schedule
import trafilatura
from gnews import GNews
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib
from flask import Flask, jsonify

# ====================== CONFIG (from environment variables) ======================

def env(name, required=True, default=None):
    val = os.environ.get(name, default)
    if required and not val:
        print(f"❌ Missing required environment variable: {name}")
        sys.exit(1)
    return val

EMAIL_SENDER = env("EMAIL_SENDER")
EMAIL_PASSWORD = env("EMAIL_PASSWORD")
EMAIL_RECEIVERS = [e.strip() for e in env("EMAIL_RECEIVERS").split(",") if e.strip()]

MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "30"))
INTERVAL_HOURS = int(os.environ.get("INTERVAL_HOURS", "3"))
PORT = int(os.environ.get("PORT", "10000"))  # Render sets PORT automatically

# ====================================================

status = {
    "last_run": None,
    "last_result": "not run yet",
    "articles_sent": 0,
}

# Give trafilatura's downloader a hard timeout so one slow article can't
# hang the entire scheduler thread forever.
TRAFILATURA_CONFIG = trafilatura.settings.use_config()
TRAFILATURA_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "10")


def extract_content(url):
    try:
        downloaded = trafilatura.fetch_url(url, config=TRAFILATURA_CONFIG)
        if downloaded:
            text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
            if text:
                return text.strip()[:500] + "..." if len(text) > 500 else text.strip()
        return "Content not available"
    except Exception:
        return "Could not extract content"


def send_emails(subject, body):
    print(f"Connecting to SMTP server to send to {len(EMAIL_RECEIVERS)} recipient(s)...")
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = ", ".join(EMAIL_RECEIVERS)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Hard timeout: without this, a blocked/stalled outbound connection
        # (common on hosting platforms for SMTP ports) hangs forever with
        # no exception ever raised, which is why nothing was showing in logs.
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=20)
        print("Connected. Starting TLS...")
        server.starttls()
        print("TLS started. Logging in...")
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        print("Logged in. Sending...")
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
        server.quit()
        print(f"✅ Email sent to {len(EMAIL_RECEIVERS)} addresses")
    except smtplib.SMTPAuthenticationError as e:
        print(f"❌ Email auth error (wrong sender/app-password, or Gmail rejected login): {e}")
    except (TimeoutError, OSError) as e:
        print(f"❌ Email connection error (network/port likely blocked): {e}")
    except Exception as e:
        print(f"❌ Email error ({type(e).__name__}): {e}")


def get_geopolitical_news():
    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Starting news fetch...")
    status["last_run"] = datetime.now().isoformat()
    status["last_result"] = "running"

    try:
        _run_news_fetch()
    except Exception as e:
        import traceback
        print("❌ Unexpected error in get_geopolitical_news:", e)
        traceback.print_exc()
        status["last_result"] = f"error: {e}"


def _run_news_fetch():
    google_news = GNews(language='en', country='US', max_results=12, period='1d')

    queries = [
        "geopolitics OR strategic affairs",
        "international summit OR conference OR high-level meeting",
        "sanctions OR circular OR notification OR foreign policy or Trade order or FTA or Trade Risk"
    ]

    all_articles = []
    for q in queries:
        try:
            print(f"  querying: {q}")
            articles = google_news.get_news(q)
            print(f"  got {len(articles)} results for: {q}")
            all_articles.extend(articles)
        except Exception as e:
            print(f"❌ Error fetching query '{q}':", e)

    seen = set()
    unique = []
    for art in all_articles:
        if art['title'] not in seen:
            seen.add(art['title'])
            unique.append(art)

    selected = unique[:MAX_ARTICLES]
    print(f"Extracting content from {len(selected)} articles...")

    def process(art):
        content = extract_content(art['url'])
        return {
            "title": art['title'],
            "source": art['publisher']['title'],
            "url": art['url'],
            "content": content
        }

    processed = []
    executor = ThreadPoolExecutor(max_workers=4)
    futures = {executor.submit(process, art): art for art in selected}
    for future in futures:
        try:
            # hard cap per article so one stuck request can't stall the run
            processed.append(future.result(timeout=20))
        except Exception as e:
            art = futures[future]
            print(f"⚠️ Skipped article (timeout/error): {art.get('title', '?')} — {e}")
    # Don't wait for any straggler threads still stuck in a slow/hung
    # network call — abandon them instead of blocking here forever.
    executor.shutdown(wait=False)
    print(f"Extraction done: {len(processed)}/{len(selected)} articles processed")

    now = datetime.now().strftime('%d %b %Y | %H:%M')

    if not processed:
        print("⚠️ No articles found this run — skipping send.")
        status["last_result"] = "no articles found"
        return

    email_body = f"GEOPOLITICAL BRIEF\n{now}\n"
    email_body += "=" * 60 + "\n\n"
    for i, art in enumerate(processed, 1):
        email_body += f"{i}. {art['title']}\n"
        email_body += f"Source : {art['source']}\n"
        email_body += f"Link   : {art['url']}\n\n"
        email_body += f"{art['content']}\n"
        email_body += "-" * 50 + "\n\n"
    email_body += "Auto-generated by Python"

    send_emails(f"🌍 Geopolitical Brief — {now}", email_body)
    status["last_result"] = "sent"
    status["articles_sent"] = len(processed)
    print("Finished!\n")


# ====================== BACKGROUND SCHEDULER THREAD ======================

def scheduler_loop():
    schedule.every(INTERVAL_HOURS).hours.do(get_geopolitical_news)
    print("Sending test run now...")
    get_geopolitical_news()
    print(f"Scheduler running (every {INTERVAL_HOURS}h) in background thread...")
    while True:
        schedule.run_pending()
        time.sleep(30)


# ====================== WEB SERVER (for Render health checks) ======================

app = Flask(__name__)


@app.route("/")
def home():
    return jsonify({
        "service": "geo-news-bot",
        "status": "running",
        **status
    })


@app.route("/health")
def health():
    return "OK", 200


@app.route("/run-now")
def run_now():
    threading.Thread(target=get_geopolitical_news, daemon=True).start()
    return jsonify({"message": "Triggered a manual run"}), 202


# Start the scheduler thread at import time so it starts under gunicorn too
# (gunicorn imports this module rather than running the __main__ block).
# _scheduler_started guards against double-start if the module is reloaded.
_scheduler_started = False


def _start_scheduler_once():
    global _scheduler_started
    if not _scheduler_started:
        _scheduler_started = True
        t = threading.Thread(target=scheduler_loop, daemon=True)
        t.start()


_start_scheduler_once()


if __name__ == "__main__":
    # Local/dev run (Render's actual start command uses gunicorn instead)
    app.run(host="0.0.0.0", port=PORT)
