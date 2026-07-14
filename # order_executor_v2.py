from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"

MAX_ACCOUNTS_SAME_SIGNAL = 3

ACCOUNTS: Dict[str, Dict[str, Any]] = {
    "prop1": {
        "name": "Prop 1",
        "size": 5_000,
        "tier": "B",
        "executes": [],
        "alerts_on": ["S"],
        "sprint": False,
    },
    "prop2": {
        "name": "Prop 2",
        "size": 500,
        "tier": "B",
        "executes": ["S", "A"],
        "alerts_on": ["S", "A", "B"],
        "sprint": False,
    },
    "prop3": {
        "name": "Prop 3",
        "size": 2_500,
        "tier": "A",
        "executes": ["S", "A", "B"],
        "alerts_on": ["S", "A", "B", "C"],
        "sprint": True,
    },
    "prop4": {
        "name": "Prop 4 DRAGON",
        "size": 10_000,
        "tier": "A",
        "executes": ["S", "A", "B", "C"],
        "alerts_on": ["S", "A", "B", "C"],
        "sprint": True,
    },
    "bot": {
        "name": "Bot",
        "size": None,
        "tier": "A",
        "executes": ["S", "A", "B", "C"],
        "alerts_on": ["S", "A", "B", "C"],
        "sprint": True,
    },
}

SIZING: Dict[tuple[str, bool], float] = {
    ("S", True): 0.04,
    ("S", False): 0.02,
    ("A", True): 0.03,
    ("A", False): 0.02,
    ("B", True): 0.02,
    ("B", False): 0.01,
    ("C", True): 0.01,
    ("C", False): 0.00,
}

CAUTION_RULES = [
    "C1 red = cut immediately. No exceptions.",
    "C1 flat after candle 1 = cut.",
    "C2 flat = cut. Never hold for candle 3.",
]


def load_signals_from_bus() -> List[Dict[str, Any]]:
    if not SIGNAL_BUS_PATH.exists():
        logger.warning("No signal_bus.json found — nothing to execute")
        return []

    try:
        payload = json.loads(SIGNAL_BUS_PATH.read_text() or "{}")
    except Exception as exc:
        logger.error("Failed to read signal bus: %s", exc)
        return []

    signals = payload.get("signals", [])
    if not isinstance(signals, list):
        logger.error("signal_bus.json is invalid: 'signals' must be a list")
        return []

    return signals


def normalize_signal(raw: Dict[str, Any]) -> Dict[str, Any]:
    pair = raw.get("pair") or raw.get("label") or raw.get("symbol") or "?"
    source = raw.get("engine") or raw.get("source") or "UNKNOWN"

    return {
        "pair": pair,
        "bias": raw.get("bias", "?"),
        "grade": raw.get("grade", "F"),
        "conviction": float(raw.get("conviction", 0) or 0),
        "entry": float(raw.get("entry", 0) or 0),
        "sl": float(raw.get("sl", 0) or 0),
        "tp": float(raw.get("tp", 0) or 0),
        "source": source,
        "trend_confirmed": bool(raw.get("trend_confirmed", False)),
        "actionstate": raw.get("actionstate", "WAIT"),
    }


def should_execute(account: Dict[str, Any], grade: str) -> bool:
    return grade in account["executes"]


def should_alert(account: Dict[str, Any], grade: str) -> bool:
    return grade in account["alerts_on"]


def temporal_guard(active_count: int) -> bool:
    return active_count < MAX_ACCOUNTS_SAME_SIGNAL


def get_position_size(
    *,
    account_size: Optional[float],
    grade: str,
    trend_confirmed: bool,
    entry: float,
    sl: float,
) -> Dict[str, Any]:
    if account_size is None:
        return {"skip": True, "reason": "dynamic bot sizing not implemented yet"}

    pct = SIZING.get((grade, trend_confirmed), 0.0)
    if pct <= 0:
        return {"skip": True, "reason": "sizing=0 for this grade/trend combo"}

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return {"skip": True, "reason": "sl_dist=0 invalid signal"}

    risk_dollars = round(account_size * pct, 2)
    units = round(risk_dollars / sl_dist, 6)

    return {
        "skip": False,
        "pct": pct,
        "risk_dollars": risk_dollars,
        "units": units,
        "sl_dist": round(sl_dist, 6),
    }


def format_signal_header(signal: Dict[str, Any]) -> str:
    trend_text = "CONFIRMED" if signal["trend_confirmed"] else "unconfirmed"
    return (
        f"SIGNAL {signal['pair']} {signal['bias']} | "
        f"Grade {signal['grade']} | "
        f"Conv {signal['conviction']:.2f} | "
        f"{signal['source']} | "
        f"Trend {trend_text} | "
        f"State {signal['actionstate']}"
    )


def format_account_message(
    *,
    account_name: str,
    execute: bool,
    signal: Dict[str, Any],
    sizing: Dict[str, Any],
) -> str:
    trend_tag = " TREND" if signal["trend_confirmed"] else ""
    mode = "EXECUTE" if execute else "ALERT ONLY"

    return (
        f"{mode} | {account_name} | "
        f"{signal['pair']} {signal['bias']} {signal['grade']} "
        f"{signal['conviction']:.0%}{trend_tag}\n"
        f"Entry {signal['entry']}  SL {signal['sl']}  TP {signal['tp']}\n"
        f"Risk ${sizing['risk_dollars']} "
        f"({sizing['pct'] * 100:.0f}% bankroll) | "
        f"Units {sizing['units']} | "
        f"SL dist {sizing['sl_dist']}"
    )


def iter_accounts() -> Iterable[tuple[str, Dict[str, Any]]]:
    return ACCOUNTS.items()


def send_signal_alert(signal: Dict[str, Any]) -> None:
    if signal["grade"] not in {"S", "A"}:
        return

    subject = (
        f"{signal['pair']} {signal['source']} {signal['bias']} {signal['grade']} "
        f"{'TREND ' if signal['trend_confirmed'] else ''}"
        f"| Conv {signal['conviction']:.0%}"
    )

    body = (
        f"Grade {signal['grade']} | {signal['pair']} {signal['bias']} | {signal['source']}\n"
        f"Entry {signal['entry']}  SL {signal['sl']}  TP {signal['tp']}\n"
        f"Trend confirmed: {signal['trend_confirmed']}\n"
        f"Action state: {signal['actionstate']}\n"
        f"Conviction: {signal['conviction']:.0%}\n\n"
        f"CAUTION RULES:\n"
        + "\n".join(f"  {rule}" for rule in CAUTION_RULES)
    )

    sms = (
        f"{signal['grade']} {signal['pair']} {signal['bias']} "
        f"{signal['conviction']:.0%} "
        f"E{signal['entry']} SL{signal['sl']} TP{signal['tp']} "
        f"{'TREND' if signal['trend_confirmed'] else ''}"
    ).strip()

    fire_alerts(subject, body, sms)


def execute_signals(signals: Optional[List[Dict[str, Any]]] = None) -> None:
    """
    Read signals from signal_bus.json (or accept injected signals),
    apply account gates, size positions, and route alerts/orders.
    """
    if signals is None:
        signals = load_signals_from_bus()

    if not signals:
        logger.info("No signals in bus — silence is the message")
        return

    logger.info("=" * 60)
    logger.info("ORDER EXECUTOR V2  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("%d signal(s) to process", len(signals))
    logger.info("=" * 60)

    for raw_signal in signals:
        signal = normalize_signal(raw_signal)

        if signal["grade"] == "F":
            logger.info("SKIP %s %s — F grade", signal["pair"], signal["bias"])
            continue

        logger.info("-" * 40)
        logger.info(format_signal_header(signal))

        accounts_in_signal = 0

        for _, account in iter_accounts():
            if not temporal_guard(accounts_in_signal):
                logger.info(
                    "  %s SKIPPED — temporal guard (%d accounts already in signal)",
                    account["name"],
                    accounts_in_signal,
                )
                continue

            execute = should_execute(account, signal["grade"])
            alert = should_alert(account, signal["grade"])

            if not execute and not alert:
                logger.info(
                    "  %s — grade %s below gate, skip",
                    account["name"],
                    signal["grade"],
                )
                continue

            sizing = get_position_size(
                account_size=account["size"],
                grade=signal["grade"],
                trend_confirmed=signal["trend_confirmed"],
                entry=signal["entry"],
                sl=signal["sl"],
            )

            if sizing["skip"]:
                logger.info("  %s — skip: %s", account["name"], sizing["reason"])
                continue

            logger.info(
                "  %s",
                format_account_message(
                    account_name=account["name"],
                    execute=execute,
                    signal=signal,
                    sizing=sizing,
                ),
            )

            if execute:
                accounts_in_signal += 1
                logger.info(
                    "  %s — ORDER QUEUED (exchange integration Phase 2)",
                    account["name"],
                )

        send_signal_alert(signal)

    logger.info("=" * 60)
    logger.info("EXECUTOR COMPLETE")
    logger.info("=" * 60)

# ── ENTRY POINT ───────────────────────────────────────────────────────

if __name__ == "__main__":
    execute_signals()
