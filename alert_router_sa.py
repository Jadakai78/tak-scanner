# alert_router_sa.py - Priority Alert Router for S & A Grade Signals
# Ensures Telegram + Outlook delivery with failover redundancy

import os
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
import urllib.request
import urllib.error
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("alert_router_sa")

# ──────────────── CONFIGURATION ────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")  # Set in .env
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")     # Your Telegram chat ID

# Outlook/Office365 SMTP
OUTLOOK_EMAIL = os.getenv("OUTLOOK_EMAIL", "")           # your@outlook.com
OUTLOOK_PASSWORD = os.getenv("OUTLOOK_PASSWORD", "")     # App password
ALERT_RECIPIENT = os.getenv("ALERT_RECIPIENT", OUTLOOK_EMAIL)  # Where to send alerts

SIGNAL_BUS = Path("/app/data/signal_bus.json")
if not SIGNAL_BUS.exists():
    SIGNAL_BUS = Path("signal_bus.json")

# ──────────────── TELEGRAM PUSH ────────────────
def send_telegram(message: str) -> bool:
    """Send message via Telegram with retry logic."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured - skipping")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode()
    
    for attempt in range(3):  # 3 retries
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    logger.info(f"✅ Telegram delivered (attempt {attempt+1})")
                    return True
        except Exception as e:
            logger.warning(f"Telegram attempt {attempt+1} failed: {e}")
    
    logger.error("❌ Telegram failed after 3 attempts")
    return False

# ──────────────── OUTLOOK EMAIL PUSH ────────────────
def send_outlook(subject: str, body: str) -> bool:
    """Send email via Outlook SMTP with retry logic."""
    if not OUTLOOK_EMAIL or not OUTLOOK_PASSWORD:
        logger.warning("Outlook not configured - skipping")
        return False
    
    for attempt in range(3):  # 3 retries
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = OUTLOOK_EMAIL
            msg["To"] = ALERT_RECIPIENT
            msg["X-Priority"] = "1"  # High priority
            
            msg.attach(MIMEText(body, "plain"))
            
            with smtplib.SMTP("smtp-mail.outlook.com", 587, timeout=15) as server:
                server.starttls()
                server.login(OUTLOOK_EMAIL, OUTLOOK_PASSWORD)
                server.send_message(msg)
            
            logger.info(f"✅ Outlook email delivered (attempt {attempt+1})")
            return True
            
        except Exception as e:
            logger.warning(f"Outlook attempt {attempt+1} failed: {e}")
    
    logger.error("❌ Outlook failed after 3 attempts")
    return False

# ──────────────── ALERT DISPATCHER ────────────────
def dispatch_sa_alerts():
    """Read signal bus and send S/A grade signals to Telegram + Outlook."""
    if not SIGNAL_BUS.exists():
        logger.warning(f"Signal bus not found: {SIGNAL_BUS}")
        return
    
    try:
        bus_data = json.loads(SIGNAL_BUS.read_text())
        signals = bus_data.get("signals", [])
        
        # Filter for S and A grade signals only
        priority_signals = [s for s in signals if s.get("grade") in ["S", "A"]]
        
        if not priority_signals:
            logger.info("No S or A grade signals to push")
            return
        
        logger.info(f"Found {len(priority_signals)} S/A grade signals to push")
        
        for sig in priority_signals:
            pair = sig.get("pair", "UNKNOWN")
            grade = sig.get("grade", "?")
            signal_name = sig.get("signal_name", "")
            trend = sig.get("trend_context", "")
            score = sig.get("score", 0)
            
            # Format alert message
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            
            tg_message = (
                f"🚨 <b>PRIORITY SIGNAL [{grade}]</b>\n\n"
                f"<b>Pair:</b> {pair}\n"
                f"<b>Signal:</b> {signal_name}\n"
                f"<b>Score:</b> {score}\n"
                f"<b>Trend:</b> {trend}\n"
                f"<b>Time:</b> {timestamp}"
            )
            
            email_body = f"""
PRIORITY TAK-SCANNER SIGNAL [GRADE {grade}]

Pair: {pair}
Signal: {signal_name}
Score: {score}
Trend Context: {trend}
Timestamp: {timestamp}

This is an automated high-priority alert.
"""
            email_subject = f"🚨 [{grade}] {pair} - {signal_name}"
            
            # Dual delivery with failover
            tg_ok = send_telegram(tg_message)
            email_ok = send_outlook(email_subject, email_body)
            
            if tg_ok and email_ok:
                logger.info(f"✅ {pair} [{grade}] delivered via BOTH channels")
            elif tg_ok or email_ok:
                logger.warning(f"⚠️  {pair} [{grade}] delivered via ONE channel only")
            else:
                logger.error(f"❌ {pair} [{grade}] FAILED on BOTH channels")
        
        logger.info("Alert dispatch cycle complete")
        
    except Exception as e:
        logger.error(f"Alert dispatch error: {e}", exc_info=True)

if __name__ == "__main__":
    logger.info("Starting S/A Alert Router...")
    dispatch_sa_alerts()
