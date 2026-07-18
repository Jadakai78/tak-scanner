from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("alerts")

TELEGRAM_TOKEN = "8860741830:AAGiccCbk4dzoTq97gWIIykZVunDvkkl6ys"
TELEGRAM_CHAT_ID = "7733126931"
PUSHOVER_TOKEN = "a144kiwuifpzpjmbpjfei63dvyqfuu"
PUSHOVER_USER = "u4v2rgci4vm95ezqx4czssz2t2du6a"
YAHOO_SMTP_HOST = "smtp.mail.yahoo.com"
YAHOO_SMTP_PORT = 587
YAHOO_SMTP_USER = "tolow47@yahoo.com"
YAHOO_SMTP_APP_PASSWORD = "xvuxwkffiuhigyed"
YAHOO_TO = "tolow47@yahoo.com"
# Corporate Outlook — S-grade fallback channel
OUTLOOK_SMTP_HOST = "smtp.office365.com"
OUTLOOK_SMTP_PORT = 587
OUTLOOK_FROM = "jasonrwarr@outlook.com"
OUTLOOK_TO_ADDRS = ["blazing0478@gmail.com", "jasonrwarr@outlook.com"]
OUTLOOK_APP_PASSWORD = os.getenv("OUTLOOK_APP_PASSWORD", "")   # set in Railway env vars
SIGNAL_BUS = Path(__file__).resolve().parent / "signal_bus.json"
CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
# Grade routing: conviction score IS the grade now — these map conviction thresholds
# Sammy (≥88) → all three channels. A-tier (≥75) → Telegram + Pushover. No quiet suppression.
TELEGRAM_GRADES = {"S", "A"}
PUSHOVER_GRADES = {"S", "A"}
YAHOO_GRADES = {g.strip().upper() for g in os.getenv("YAHOO_GRADES", "S").split(",") if g.strip()}
OUTLOOK_GRADES = {"S"}   # S-grade only — corporate fallback so you never miss a Sammy


def _send_telegram(message: str) -> None:
    try:
        payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Telegram sent HTTP %s", resp.status)
    except Exception as exc:
        logger.warning("Telegram failed: %s", exc)


def _send_pushover(title: str, message: str, priority: int = 0) -> None:
    try:
        payload = urllib.parse.urlencode({"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "title": title, "message": message, "priority": priority}).encode()
        req = urllib.request.Request("https://api.pushover.net/1/messages.json", data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Pushover sent HTTP %s", resp.status)
    except Exception as exc:
        logger.warning("Pushover failed: %s", exc)


def _send_yahoo(subject: str, message: str) -> None:
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = YAHOO_SMTP_USER
        msg["To"] = YAHOO_TO
        msg.set_content(message.replace("*", ""))
        with smtplib.SMTP(YAHOO_SMTP_HOST, YAHOO_SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(YAHOO_SMTP_USER, YAHOO_SMTP_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info("Yahoo sent OK")
    except Exception as exc:
        logger.warning("Yahoo failed: %s", exc)


def _send_outlook(subject: str, message: str) -> None:
    """Corporate fallback — fires on S-grade to both addresses simultaneously."""
    if not OUTLOOK_APP_PASSWORD:
        logger.warning("Outlook skipped — OUTLOOK_APP_PASSWORD not set in env")
        return
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = OUTLOOK_FROM
        msg["To"] = ", ".join(OUTLOOK_TO_ADDRS)
        msg.set_content(message.replace("*", ""))
        with smtplib.SMTP(OUTLOOK_SMTP_HOST, OUTLOOK_SMTP_PORT, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(OUTLOOK_FROM, OUTLOOK_APP_PASSWORD)
            smtp.send_message(msg)
        logger.info("Outlook sent OK → %s", OUTLOOK_TO_ADDRS)
    except Exception as exc:
        logger.warning("Outlook failed: %s", exc)


def push_to_cf() -> None:
    if not SIGNAL_BUS.exists():
        logger.warning("push_to_cf: signal_bus.json not found")
        return
    try:
        payload = SIGNAL_BUS.read_bytes()
        req = urllib.request.Request(CF_KV_URL, data=payload, method="PUT", headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("CF KV push OK HTTP %s", resp.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        logger.error("CF KV push error: %s %s - %s", e.code, e.reason, body)
    except Exception as exc:
        logger.error("CF KV push failed: %s", exc)


def _format_position_sizing(signal: Dict[str, Any]) -> str:
    sizing = signal.get("position_sizing") or {}
    if not sizing:
        return "Sizing: N/A"
    lines = []
    for lane_key in ["Dragon", "Starter3", "Starter2", "Eval1"]:
        row = sizing.get(lane_key)
        if not row:
            continue
        label = row.get("label", lane_key)
        if row.get("mode") == "PROTECT_ONLY":
            lines.append(f"{label}: PROTECT ONLY")
        else:
            lines.append(f"{label}: {row['units']:.4f} units | ${row['dollar_risk']:.2f} risk")
    return "\n".join(lines) if lines else "Sizing: N/A"


def _format_signal(signal: Dict[str, Any], quiet: bool = False) -> str:
    grade = signal.get("grade", "?")
    pair = signal.get("pair", "?")
    bias = signal.get("bias", "?")
    engine = signal.get("engine", "?")
    entry = signal.get("entry", "?")
    sl = signal.get("sl", "?")
    tp = signal.get("tp", "?")
    rr = signal.get("rr", "?")
    regime = signal.get("regime", "?")
    conv = signal.get("final_conviction", signal.get("conviction", "?"))
    tier = signal.get("tier", "?")
    remi = signal.get("remi_status", "CLEAN")
    tag = " [CAUTION]" if remi == "CAUTION" else ""
    quiet_tag = " [QUIET]" if quiet else ""
    sizing_text = _format_position_sizing(signal)
    return (
        f"*{grade}-GRADE{quiet_tag}{tag}*\n"
        f"*{pair}* {bias} - {tier}\n"
        f"Engine: {engine} | Conv: {conv}\n"
        f"Entry: {entry} | SL: {sl} | TP: {tp} | R:R {rr}\n"
        f"Regime: {regime}\n"
        f"{sizing_text}"
    )


def fire_alerts(signals: List[Dict[str, Any]], quiet: bool = False, sprint_mode: bool = False) -> None:
    """Fire alerts on all channels. quiet flag is IGNORED — feed runs 24/7.

    Channel routing:
      Telegram  → S + A grade
      Pushover  → S + A grade  (S = priority 1, A = priority 0)
      Yahoo     → S grade only
      Outlook   → S grade only — both blazing0478@gmail.com + jasonrwarr@outlook.com
    """
    logger.info("fire_alerts called count=%s sprint=%s", len(signals), sprint_mode)
    for signal in signals:
        grade = str(signal.get("grade", "")).upper()
        pair  = signal.get("pair", "?")
        bias  = signal.get("bias", "?")
        engine = signal.get("engine", "?")
        conv  = signal.get("final_conviction", signal.get("conviction", "?"))
        remi  = signal.get("remi_status", "CLEAN")
        msg   = _format_signal(signal, quiet=False)   # quiet suppression removed

        # Telegram — S + A, always fires regardless of hour
        if grade in TELEGRAM_GRADES:
            _send_telegram(msg)

        # Pushover — S + A
        if grade in PUSHOVER_GRADES:
            priority = 1 if grade == "S" else 0
            title = f"JHL {grade}-Grade: {pair} {bias}"
            _send_pushover(title, msg.replace("*", ""), priority=priority)

        # Yahoo — S only
        if grade in YAHOO_GRADES:
            title = f"JHL S-Grade SAMMY: {pair} {bias}"
            _send_yahoo(title, msg)

        # Outlook — S only, corporate fallback to both inboxes
        if grade in OUTLOOK_GRADES:
            title = f"JHL SAMMY ALERT: {pair} {bias} | Conv: {conv}"
            _send_outlook(title, msg)

        logger.info(
            "[ALERT] %s %s %s grade=%s conv=%s remi=%s | telegram=%s pushover=%s yahoo=%s outlook=%s",
            pair, bias, engine, grade, conv, remi,
            grade in TELEGRAM_GRADES, grade in PUSHOVER_GRADES,
            grade in YAHOO_GRADES, grade in OUTLOOK_GRADES,
        )
    push_to_cf()
