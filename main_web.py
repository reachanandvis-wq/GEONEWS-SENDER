# ============================================================
# Geopolitical News Bot → WhatsApp + Multiple Emails
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

PHONE = env("PHONE")
APIKEY = env("APIKEY")
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


def send_whatsapp(message):
    try:
        if len(message) > 3900:
            message = message[:3900] + "\n\n...(truncated)"
        url = f"https://api.callmebot.com/whatsapp.php?phone={PHONE}&text={requests.utils.quote(message)}&apikey={APIKEY}"
        r = requests.get(url, timeout=20)
        print("✅ WhatsApp sent" if r.status_code == 200 else f"❌ WhatsApp failed: {r.text}")
    except Exception as e:
        print("❌ WhatsApp error:", e)


def send_emails(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = ", ".join(EMAIL_RECEIVERS)
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVERS, msg.as_string())
        server.quit()
        print(f"✅ Email sent to {len(EMAIL_RECEIVERS)} addresses")
    except Exception as e:
        print("❌ Email error:", e)


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
            articles = google_news.get_news(q)
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
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(process, art): art for art in selected}
        for future in futures:
            try:
                # hard cap per article so one stuck request can't stall the run
                processed.append(future.result(timeout=20))
            except Exception as e:
                art = futures[future]
                print(f"⚠️ Skipped article (timeout/error): {art.get('title', '?')} — {e}")

    now = datetime.now().strftime('%d %b %Y | %H:%M')

    if not processed:
        print("⚠️ No articles found this run — skipping send.")
        status["last_result"] = "no articles found"
        return

    wa_msg = f"🌍 *Geopolitical Brief*\n{now}\n\n"
    for i, art in enumerate(processed, 1):
        wa_msg += f"*{i}. {art['title'][:75]}*\n"
        wa_msg += f"_{art['source']}_\n"
        wa_msg += f"{art['content'][:300]}...\n\n"
    wa_msg += "_Auto generated_"

    email_body = f"GEOPOLITICAL BRIEF\n{now}\n"
    email_body += "=" * 60 + "\n\n"
    for i, art in enumerate(processed, 1):
        email_body += f"{i}. {art['title']}\n"
        email_body += f"Source : {art['source']}\n"
        email_body += f"Link   : {art['url']}\n\n"
        email_body += f"{art['content']}\n"
        email_body += "-" * 50 + "\n\n"
    email_body += "Auto-generated by Python"

    send_whatsapp(wa_msg)
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
