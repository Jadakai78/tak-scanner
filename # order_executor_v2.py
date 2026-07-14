# order_executor_v2.py — JHL Holdings
# Grade-based execution gates + position sizing by tier/grade/trend.
# Reads signal_bus.json, applies account gates, sizes positions,
# routes to correct accounts. One function. One pass.
# July 5, 2026 — Probability collapse build.

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from alerts import fire_alerts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler("executor.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("executor")

MODULE_DIR      = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"

# ── ACCOUNT DEFINITIONS ───────────────────────────────────────────────

ACCOUNTS = {
    "prop1": {
        "name":      "Prop 1",
        "size":      5_000,
        "tier":      "B",
        "executes":  [],           # Alert only on S — no auto-execute
        "alerts_on": ["S"],
        "sprint":    False,
    },
    "prop2": {
        "name":      "Prop 2",
        "size":      500,
        "tier":      "B",
        "executes":  ["S", "A"],
        "alerts_on": ["S", "A", "B"],  # B = alert only
        "sprint":    False,
    },
    "prop3": {
        "name":      "Prop 3",
        "size":      2_500,
        "tier":      "A",
        "executes":  ["S", "A", "B"],
        "alerts_on": ["S", "A", "B", "C"],  # C = alert only
        "sprint":    True,
    },
    "prop4": {
        "name":      "Prop 4 DRAGON",
        "size":      10_000,
        "tier":      "A",
        "executes":  ["S", "A", "B", "C"],
        "alerts_on": ["S", "A", "B", "C"],
        "sprint":    True,
    },
    "bot": {
        "name":      "Bot",
        "size":      None,   # Reads from signal sizing
        "tier":      "A",
        "executes":  ["S", "A", "B", "C"],
        "alerts_on": ["S", "A", "B", "C"],
        "sprint":    True,
    },
}

# ── POSITION SIZING ───────────────────────────────────────────────────

SIZING = {
    ("S", True):  0.04,
    ("S", False): 0.02,
    ("A", True):  0.03,
    ("A", False): 0.02,
    ("B", True):  0.02,
    ("B", False): 0.01,
    ("C", True):  0.01,
    ("C", False): 0.00,   # Skip
}

# ── CAUTION RULES (applied to every open position) ───────────────────

CAUTION_RULES = [
    "C1 red = cut immediately. No exceptions.",
    "C1 flat after candle 1 = cut.",
    "C2 flat = cut. Never hold for candle 3.",
]

# ── TEMPORAL DIVERSIFICATION GUARD ───────────────────────────────────

MAX_ACCOUNTS_SAME_SIGNAL = 3  # Never all 4 props in same signal


def get_position_size(
    account_size: float,
    grade: str,
    trend_confirmed: bool,
    entry: float,
    sl: float,
) -> Dict[str, Any]:
    pct = SIZING.get((grade, trend_confirmed), 0.0)
    if pct == 0 or account_size is None:
        return {"skip": True, "reason": "sizing=0 for this grade/trend combo"}

    risk_dollars = round(account_size * pct, 2)
    sl_dist      = abs(entry - sl)
    if sl_dist == 0:
        return {"skip": True, "reason": "sl_dist=0 invalid signal"}

    units = round(risk_dollars / sl_dist, 6)
    tp_profit = 0
    return {
        "skip":          False,
        "pct":           pct,
        "risk_dollars":  risk_dollars,
        "units":         units,
        "sl_dist":       round(sl_dist, 6),
    }


def should_execute(account: Dict, grade: str) -> bool:
    return grade in account["executes"]


def should_alert(account: Dict, grade: str) -> bool:
    return grade in account["alerts_on"]


def temporal_guard(signal: Dict, active_count: int) -> bool:
    """Returns True if safe to add another account to this signal."""
    return active_count < MAX_ACCOUNTS_SAME_SIGNAL


# ── MAIN EXECUTOR ─────────────────────────────────────────────────────

def execute_signals(signals: Optional[List[Dict]] = None):
    """
    Read signal_bus.json, apply gates, size positions,
    route to accounts. One pass. One function.
    """
    if signals is None:
        if not SIGNAL_BUS_PATH.exists():
            logger.warning("No signal_bus.json found — nothing to execute")
            return
        try:
            bus     = json.loads(SIGNAL_BUS_PATH.read_text() or "{}")
            signals = bus.get("signals", [])
        except Exception as e:
            logger.error("Failed to read signal_bus: %s", e)
            return

    if not signals:
        logger.info("No signals in bus — silence is the message")
        return

    logger.info("=" * 60)
    logger.info("ORDER EXECUTOR V2  %s",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S CDT"))
    logger.info("%d signal(s) to process", len(signals))
    logger.info("=" * 60)

    for signal in signals:
        grade           = signal.get("grade", "F")
        pair            = signal.get("label", signal.get("symbol", "?"))
        bias            = signal.get("bias", "?")
        conviction      = signal.get("conviction", 0)
        entry           = signal.get("entry", 0)
        sl              = signal.get("sl", 0)
        tp              = signal.get("tp", 0)
        trend_confirmed = signal.get("trend_confirmed", False)
        source          = signal.get("source", "T1_4H")

        if grade == "F":
            logger.info("SKIP %s %s — F grade", pair, bias)
            continue

        logger.info("-" * 40)
        logger.info(
            "SIGNAL %s %s | Grade %s | Conv %.2f | %s | Trend %s",
            pair, bias, grade, conviction, source,
            "CONFIRMED" if trend_confirmed else "unconfirmed",
        )

        accounts_in_signal = 0

        for acct_key, account in ACCOUNTS.items():

            # Temporal diversification guard
            if not temporal_guard(signal, accounts_in_signal):
                logger.info(
                    "  %s SKIPPED — temporal guard (%d accounts already in signal)",
                    account["name"], accounts_in_signal,
                )
                continue

            execute = should_execute(account, grade)
            alert   = should_alert(account, grade)

            if not execute and not alert:
                logger.info("  %s — grade %s below gate, skip",
                            account["name"], grade)
                continue

            sizing = get_position_size(
                account_size    = account["size"] or 0,
                grade           = grade,
                trend_confirmed = trend_confirmed,
                entry           = entry,
                sl              = sl,
            )

            if sizing.get("skip"):
                logger.info("  %s — skip: %s",
                            account["name"], sizing.get("reason"))
                continue

            trend_tag   = "TREND" if trend_confirmed else ""
            alert_label = "EXECUTE" if execute else "ALERT ONLY"

            msg = (
                f"{alert_label} | {account['name']} | "
                f"{pair} {bias} {grade} {conviction:.0%} {trend_tag}\n"
                f"Entry {entry}  SL {sl}  TP {tp}\n"
                f"Risk ${sizing['risk_dollars']} "
                f"({sizing['pct']*100:.0f}% bankroll) | "
                f"Units {sizing['units']} | "
                f"SL dist {sizing['sl_dist']}"
            )

            logger.info("  %s", msg)

            if execute:
                accounts_in_signal += 1
                # Wire Kraken / exchange execution here in Phase 2
                logger.info(
                    "  %s — ORDER QUEUED (exchange integration Phase 2)",
                    account["name"],
                )

        # Fire consolidated alert for this signal
        if grade in ("S", "A"):
            subject = (
                f"{pair} {source} {bias} {grade} "
                f"{'TREND ' if trend_confirmed else ''}"
                f"| Conv {conviction:.0%}"
            )
            body = (
                f"Grade {grade} | {pair} {bias} | {source}\n"
                f"Entry {entry}  SL {sl}  TP {tp}\n"
                f"Trend confirmed: {trend_confirmed}\n"
                f"Conviction: {conviction:.0%}\n\n"
                f"CAUTION RULES:\n" +
                "\n".join(f"  {r}" for r in CAUTION_RULES)
            )
            sms = (
                f"{grade} {pair} {bias} {conviction:.0%} "
                f"E{entry} SL{sl} TP{tp} "
                f"{'TREND' if trend_confirmed else ''}"
            )
            fire_alerts(subject, body, sms)

    logger.info("=" * 60)
    logger.info("EXECUTOR COMPLETE")
    logger.info("=" * 60)


# ── ENTRY POINT ───────────────────────────────────────────────────────

if __name__ == "__main__":
    execute_signals()