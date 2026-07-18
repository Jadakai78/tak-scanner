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

# ── Credentials ───────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = "8860741830:AAGiccCbk4dzoTq97gWIIykZVunDvkkl6ys"
TELEGRAM_CHAT_ID  = "7733126931"
PUSHOVER_TOKEN    = "a144kiwuifpzpjmbpjfei63dvyqfuu"
PUSHOVER_USER     = "u4v2rgci4vm95ezqx4czssz2t2du6a"
YAHOO_SMTP_HOST   = "smtp.mail.yahoo.com"
YAHOO_SMTP_PORT   = 587
YAHOO_SMTP_USER   = "tolow47@yahoo.com"
YAHOO_SMTP_PASS   = "xvuxwkffiuhigyed"
YAHOO_TO          = "tolow47@yahoo.com"
OUTLOOK_SMTP_HOST = "smtp.office365.com"
OUTLOOK_SMTP_PORT = 587
OUTLOOK_FROM      = "jasonrwarr@outlook.com"
OUTLOOK_TO_ADDRS  = ["blazing0478@gmail.com", "jasonrwarr@outlook.com"]
OUTLOOK_APP_PASS  = os.getenv("OUTLOOK_APP_PASSWORD", "")

SIGNAL_BUS    = Path(__file__).resolve().parent / "signal_bus.json"
CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID   = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN  = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL     = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
)

# ── Grade routing — 24/7, no quiet suppression ───────────────────────────────
# S (Sammy ≥88)  → Pushover + Telegram + Yahoo + Outlook
# A (≥75)        → Pushover + Telegram
# KILL           → treated same as S — all channels
TELEGRAM_GRADES = {"S", "A", "KILL"}
PUSHOVER_GRADES = {"S", "A", "KILL"}
YAHOO_GRADES    = {"S", "KILL"}
OUTLOOK_GRADES  = {"S", "KILL"}


# ── Transport helpers ─────────────────────────────────────────────────────────

def _send_telegram(message: str) -> None:
    try:
        payload = json.dumps({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Telegram sent HTTP %s", resp.status)
    except Exception as exc:
        logger.warning("Telegram failed: %s", exc)


def _send_pushover(title: str, message: str, priority: int = 0) -> None:
    try:
        payload = urllib.parse.urlencode({
            "token":    PUSHOVER_TOKEN,
            "user":     PUSHOVER_USER,
            "title":    title,
            "message":  message,
            "priority": priority,
        }).encode()
        req = urllib.request.Request(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Pushover sent HTTP %s", resp.status)
    except Exception as exc:
        logger.warning("Pushover failed: %s", exc)


def _send_email(smtp_host: str, smtp_port: int, user: str, password: str,
                from_addr: str, to_addrs: List[str],
                subject: str, body: str) -> None:
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"]    = from_addr
        msg["To"]      = ", ".join(to_addrs)
        msg.set_content(body)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.ehlo(); smtp.starttls(); smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
        logger.info("Email sent OK → %s", to_addrs)
    except Exception as exc:
        logger.warning("Email failed (%s): %s", smtp_host, exc)


# ── Format helpers ────────────────────────────────────────────────────────────

def _fmt_pushover(signal: Dict[str, Any], is_kill: bool = False) -> tuple[str, str]:
    """Pushover: one-line popup. Title + short body only."""
    pair   = signal.get("pair", "?")
    bias   = signal.get("bias", "?")
    conv   = signal.get("conviction", signal.get("final_conviction", "?"))
    engine = signal.get("engine", "?")
    grade  = "KILL" if is_kill else signal.get("grade", "?")
    action = signal.get("action_state", signal.get("intent", ""))

    title = f"{'🔴 KILL' if is_kill else '🟢 JHL'} {grade}: {pair} {bias}"
    body  = f"{engine} | Conv {conv} | {action}"
    return title, body


def _fmt_telegram(signal: Dict[str, Any], is_kill: bool = False) -> str:
    """Telegram: entry details block. Readable on phone while driving."""
    pair   = signal.get("pair", "?")
    bias   = signal.get("bias", "?")
    engine = signal.get("engine", "?")
    entry  = signal.get("entry", "?")
    sl     = signal.get("sl", "?")
    tp     = signal.get("tp", "?")
    rr     = signal.get("rr", "?")
    conv   = signal.get("conviction", signal.get("final_conviction", "?"))
    regime = signal.get("regime", "?")
    action = signal.get("action_state", signal.get("intent", ""))
    sizing = signal.get("risk_mode", signal.get("position_sizing_mode", ""))
    risk_pct = signal.get("risk_percent", "")
    grade  = "KILL" if is_kill else signal.get("grade", "?")

    header = f"🔴 *KILL SIGNAL*" if is_kill else f"*{grade}-GRADE*"
    size_line = f"Size: {sizing} ({risk_pct}%)" if sizing else ""

    lines = [
        header,
        f"*{pair}* {bias}  |  {engine}",
        f"Entry: `{entry}`  SL: `{sl}`  TP: `{tp}`",
        f"R:R {rr}  |  Conv {conv}",
        f"Regime: {regime}  |  {action}",
    ]
    if size_line:
        lines.append(size_line)
    return "\n".join(lines)


def _fmt_email(signal: Dict[str, Any], is_kill: bool = False) -> tuple[str, str]:
    """Email: full breakdown — all fields, scoring, trap context."""
    pair    = signal.get("pair", "?")
    bias    = signal.get("bias", "?")
    engine  = signal.get("engine", "?")
    regime  = signal.get("regime", "?")
    grade   = "KILL" if is_kill else signal.get("grade", "?")
    conv    = signal.get("conviction", signal.get("final_conviction", "?"))
    entry   = signal.get("entry", "?")
    sl      = signal.get("sl", "?")
    tp      = signal.get("tp", "?")
    rr      = signal.get("rr", "?")
    action  = signal.get("action_state", signal.get("intent", ""))
    reason  = signal.get("action_reason", "")
    mtf     = signal.get("mtf_alignment", signal.get("mtf", ""))
    # Scoring
    def_s   = signal.get("defensive_score", "")
    off_s   = signal.get("offensive_score", "")
    trap    = signal.get("trap_risk", signal.get("trap_score", ""))
    bonus   = signal.get("bonus_multiplier", "")
    b_reasons = signal.get("bonus_reasons", [])
    # Microstructure
    fakeout = signal.get("fakeout_probability", "")
    chain   = signal.get("liquidation_chain_potential", "")
    ob_q    = signal.get("ob_quality", "")
    fvg_p   = signal.get("fvg_path_score", "")
    fvg_ct  = signal.get("fvg_count_in_path", "")
    # RTS / trap context
    trap_sig = signal.get("trap_signature", signal.get("fakeout_signature", ""))
    delta    = signal.get("delta_bias", "")
    # Sizing
    risk_mode = signal.get("risk_mode", signal.get("position_sizing_mode", ""))
    risk_pct  = signal.get("risk_percent", "")

    subject = f"JHL {'KILL' if is_kill else grade+'-GRADE'}: {pair} {bias} | Conv {conv}"

    body = f"""
{'='*60}
JHL Holdings — {'KILL POSITION' if is_kill else 'SIGNAL ALERT'}
{'='*60}

PAIR:    {pair}
BIAS:    {bias}
ENGINE:  {engine}
REGIME:  {regime}
GRADE:   {grade}
ACTION:  {action}
REASON:  {reason}

LEVELS
  Entry:  {entry}
  SL:     {sl}
  TP:     {tp}
  R:R:    {rr}

SIZING
  Mode:   {risk_mode}
  Risk %: {risk_pct}

CONVICTION SCORING
  Conviction:       {conv}
  Defensive Score:  {def_s}
  Offensive Score:  {off_s}
  Trap Risk:        {trap}
  Bonus Mult:       {bonus}
  Bonus Reasons:    {', '.join(b_reasons) if b_reasons else 'none'}

MICROSTRUCTURE
  Fakeout Prob:     {fakeout}
  Chain Potential:  {chain}
  OB Quality:       {ob_q}
  FVG Path Score:   {fvg_p}  ({fvg_ct} open FVGs in path)
  Fakeout Sig:      {trap_sig}

CONTEXT
  MTF Alignment:    {mtf}
  Delta Bias:       {delta}

{'='*60}
Raw signal keys: {list(signal.keys())}
""".strip()

    return subject, body


# ── CF KV push ────────────────────────────────────────────────────────────────

def push_to_cf() -> None:
    if not SIGNAL_BUS.exists():
        logger.warning("push_to_cf: signal_bus.json not found")
        return
    try:
        payload = SIGNAL_BUS.read_bytes()
        req = urllib.request.Request(
            CF_KV_URL, data=payload, method="PUT",
            headers={"Authorization": f"Bearer {CF_API_TOKEN}",
                     "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("CF KV push OK HTTP %s", resp.status)
    except urllib.error.HTTPError as e:
        logger.error("CF KV push error: %s %s", e.code,
                     e.read().decode("utf-8", errors="ignore")[:200])
    except Exception as exc:
        logger.error("CF KV push failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────────

def fire_alerts(
    signals: List[Dict[str, Any]],
    quiet: bool = False,          # ignored — 24/7
    sprint_mode: bool = False,
) -> None:
    """Fire alerts. quiet flag is permanently ignored — 24/7 operation.

    Channel routing:
      Pushover → S, A, KILL  — generic popup (title + one line)
      Telegram → S, A, KILL  — entry block (pair, entry, sl, tp, rr, conv)
      Yahoo    → S, KILL     — full breakdown email
      Outlook  → S, KILL     — full breakdown to BOTH inboxes simultaneously
    """
    logger.info("fire_alerts count=%s sprint=%s", len(signals), sprint_mode)

    for signal in signals:
        grade    = str(signal.get("grade", "")).upper()
        is_kill  = grade == "KILL" or signal.get("kill_type") is not None
        eff_grade = "KILL" if is_kill else grade
        pair     = signal.get("pair", "?")
        bias     = signal.get("bias", "?")
        engine   = signal.get("engine", "?")
        conv     = signal.get("conviction", signal.get("final_conviction", "?"))

        # Pushover — popup only
        if eff_grade in PUSHOVER_GRADES:
            po_title, po_body = _fmt_pushover(signal, is_kill=is_kill)
            priority = 1 if eff_grade in {"S", "KILL"} else 0
            _send_pushover(po_title, po_body, priority=priority)

        # Telegram — entry block
        if eff_grade in TELEGRAM_GRADES:
            _send_telegram(_fmt_telegram(signal, is_kill=is_kill))

        # Yahoo — full breakdown
        if eff_grade in YAHOO_GRADES:
            subj, body = _fmt_email(signal, is_kill=is_kill)
            _send_email(
                YAHOO_SMTP_HOST, YAHOO_SMTP_PORT,
                YAHOO_SMTP_USER, YAHOO_SMTP_PASS,
                YAHOO_SMTP_USER, [YAHOO_TO],
                subj, body,
            )

        # Outlook — full breakdown to both inboxes
        if eff_grade in OUTLOOK_GRADES and OUTLOOK_APP_PASS:
            subj, body = _fmt_email(signal, is_kill=is_kill)
            _send_email(
                OUTLOOK_SMTP_HOST, OUTLOOK_SMTP_PORT,
                OUTLOOK_FROM, OUTLOOK_APP_PASS,
                OUTLOOK_FROM, OUTLOOK_TO_ADDRS,
                subj, body,
            )
        elif eff_grade in OUTLOOK_GRADES and not OUTLOOK_APP_PASS:
            logger.warning("Outlook skipped — OUTLOOK_APP_PASSWORD not in env")

        logger.info(
            "[ALERT] %s %s %s grade=%s kill=%s conv=%s | "
            "po=%s tg=%s yahoo=%s outlook=%s",
            pair, bias, engine, eff_grade, is_kill, conv,
            eff_grade in PUSHOVER_GRADES, eff_grade in TELEGRAM_GRADES,
            eff_grade in YAHOO_GRADES,
            eff_grade in OUTLOOK_GRADES and bool(OUTLOOK_APP_PASS),
        )

    push_to_cf()
