"""
position_monitor.py — Council eyes on live positions.

Runs as a daemon thread inside server.py.
Every POLL_INTERVAL seconds it checks every executed position against:
  1. Current price vs SL (hard stop breach)
  2. Price flatness — position going nowhere (FLAT kill)
  3. TrapDetector caution gate — trap_score rising on this pair's RTS signals
  4. April mode — if STAND_DOWN, all open positions get a CAUTION alert

On KILL condition: fires Pushover priority-1 + Telegram + email immediately.
On CAUTION: fires Pushover standard + Telegram (no email — not a kill yet).
"""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

logger = logging.getLogger("position_monitor")

# ── Config ────────────────────────────────────────────────────────────────────
POLL_INTERVAL      = 60          # seconds between checks
FLAT_CANDLES       = 2           # candles with no meaningful move → FLAT kill
FLAT_THRESHOLD_PCT = 0.0015      # 0.15% move counts as "meaningful"
SIGNAL_BUS_PATH    = Path("/app/data/signal_bus.json")
KRAKEN_TICKER_URL  = "https://api.kraken.com/0/public/Ticker"

# Track per-pair caution state across cycles
_caution_candles: Dict[str, int] = {}   # pair → consecutive flat/caution candle count
_alerted_kills:   set             = set()  # pairs already KILL-alerted this position


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(val: Any, default: float = 0.0) -> float:
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _get_current_prices(pairs: List[str]) -> Dict[str, float]:
    """Fetch last trade price for each pair from Kraken public ticker."""
    prices: Dict[str, float] = {}
    if not pairs:
        return prices
    try:
        resp = requests.get(
            KRAKEN_TICKER_URL,
            params={"pair": ",".join(pairs)},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("result", {})
        for key, val in data.items():
            # last trade price is val["c"][0]
            price = _safe(val.get("c", [0])[0])
            # Map Kraken's internal pair name back — try direct match first
            prices[key] = price
            # Also store under the original requested pair name if different
            for p in pairs:
                if p.upper() in key.upper() or key.upper() in p.upper():
                    prices[p] = price
    except Exception as exc:
        logger.warning("Ticker fetch failed: %s", exc)
    return prices


def _build_kill_signal(position: Dict[str, Any], reason: str, kill_type: str,
                        current_price: float) -> Dict[str, Any]:
    """Construct a KILL signal dict compatible with fire_alerts."""
    return {
        "pair":           position.get("pair", "?"),
        "bias":           position.get("bias", "?"),
        "engine":         position.get("engine", "Council"),
        "grade":          "KILL",
        "kill_type":      kill_type,
        "kill_reason":    reason,
        "entry":          _safe(position.get("entry", 0)),
        "sl":             _safe(position.get("sl", 0)),
        "tp":             _safe(position.get("tp", 0)),
        "conviction":     _safe(position.get("conviction", 0)),
        "current_price":  current_price,
        "rr":             _safe(position.get("rr", 0)),
        "regime":         position.get("regime", "?"),
        "action_state":   "KILL",
        "action_reason":  reason,
        "trap_risk":      position.get("trap_risk", 0),
        "fired_at":       _utc_now(),
        "mtf_verdict":    position.get("mtf_verdict", ""),
        "defensive_score": position.get("defensive_score", 0),
        "offensive_score": position.get("offensive_score", 0),
        "bonus_multiplier": position.get("bonus_multiplier", 1.0),
        "bonus_reasons":  position.get("bonus_reasons", []),
    }


def _check_position(position: Dict[str, Any], current_price: float,
                    rts_signals: List[Dict[str, Any]]) -> tuple[str, str]:
    """
    Returns (verdict, reason).
    verdict: "KILL" | "CAUTION" | "CLEAR"
    """
    pair  = position.get("pair", "?")
    bias  = position.get("bias", "LONG")
    entry = _safe(position.get("entry", 0))
    sl    = _safe(position.get("sl", 0))
    tp    = _safe(position.get("tp", 0))

    if entry == 0 or current_price == 0:
        return "CLEAR", ""

    # ── 1. Hard SL breach ─────────────────────────────────────────────────
    if bias == "LONG" and current_price <= sl:
        return "KILL", f"SL breached — price {current_price:.4f} ≤ SL {sl:.4f}"
    if bias == "SHORT" and current_price >= sl:
        return "KILL", f"SL breached — price {current_price:.4f} ≥ SL {sl:.4f}"

    # ── 2. Flat detection (C1/C2 rule) ───────────────────────────────────
    move_pct = abs(current_price - entry) / entry if entry else 0
    if move_pct < FLAT_THRESHOLD_PCT:
        _caution_candles[pair] = _caution_candles.get(pair, 0) + 1
        if _caution_candles[pair] >= FLAT_CANDLES:
            return "KILL", (
                f"FLAT — {_caution_candles[pair]} candles with <{FLAT_THRESHOLD_PCT*100:.2f}% "
                f"move from entry {entry:.4f} (current {current_price:.4f})"
            )
        return "CAUTION", f"Flat C{_caution_candles[pair]} — watching {pair}"
    else:
        # Reset flat counter if price is moving
        _caution_candles[pair] = 0

    # ── 3. Trap detector — RTS signal for this pair ───────────────────────
    pair_rts = [r for r in rts_signals if r.get("pair") == pair]
    for rts in pair_rts:
        trap_score = _safe(rts.get("trap_score", 0))
        intent     = str(rts.get("intent", "")).upper()
        action     = str(rts.get("action_state", "")).upper()

        # Hard trap
        if trap_score >= 0.75 and intent in {"TRAP", "ATTACKTRAP", "FAKEOUT", "ATTACK_TRAP"}:
            return "KILL", (
                f"TRAP DETECTED — RTS {rts.get('engine','?')} trap_score={trap_score:.2f} "
                f"intent={intent}"
            )
        # Council WAIT/CUT intent
        if action in {"CUT", "WAIT"} and trap_score >= 0.55:
            return "CAUTION", (
                f"RTS {rts.get('engine','?')} action={action} trap={trap_score:.2f} — "
                f"watching {pair}"
            )

    # ── 4. Conviction decay — live position conviction dropped below floor ─
    # Re-read the current bus to get the freshest conviction for this pair.
    # If the scanner rescored the pair and conviction fell below 70,
    # or trap crept to ≥0.65, cut immediately.
    CONVICTION_FLOOR = 70.0
    TRAP_CAUTION_GATE = 0.65
    TRAP_KILL_GATE    = 0.75

    try:
        import json, pathlib
        bus_path = Path("/app/data/signal_bus.json")
        if bus_path.exists():
            bus_data = json.loads(bus_path.read_text())
            for sig in bus_data.get("signals", []):
                if sig.get("pair") != pair:
                    continue
                # Normalize conviction to 0-100
                raw_conv = _safe(sig.get("conviction", sig.get("final_conviction", 100)))
                conv = raw_conv * 100 if raw_conv <= 1.0 else raw_conv
                sig_trap = _safe(sig.get("trap_risk", sig.get("trap_score", 0)))

                if sig_trap >= TRAP_KILL_GATE:
                    return "KILL", (
                        f"CONVICTION KILL — trap_score {sig_trap:.2f} ≥ {TRAP_KILL_GATE} "
                        f"on LIVE {pair} position"
                    )
                if conv < CONVICTION_FLOOR:
                    return "KILL", (
                        f"CONVICTION DECAY — conv {conv:.1f} fell below floor {CONVICTION_FLOOR} "
                        f"on LIVE {pair} position"
                    )
                if sig_trap >= TRAP_CAUTION_GATE:
                    return "CAUTION", (
                        f"TRAP CREEP — trap_score {sig_trap:.2f} ≥ {TRAP_CAUTION_GATE} "
                        f"on {pair} — watching"
                    )
    except Exception as _conv_err:
        logger.debug("Conviction decay check failed %s: %s", pair, _conv_err)

    return "CLEAR", ""


def _get_april_mode(bus: Dict[str, Any]) -> str:
    """Read April's current council_mode from the bus."""
    audit = bus.get("audit", {})
    april = audit.get("april_view", {})
    return april.get("council_mode", "NORMAL")


def run_monitor() -> None:
    """Main loop — called as daemon thread from server.py."""
    logger.info("Position monitor started — poll every %ds", POLL_INTERVAL)

    # Lazy import alerts to avoid circular import at module load
    try:
        from alerts import fire_alerts  # type: ignore
    except ImportError:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from alerts import fire_alerts  # type: ignore
        except ImportError as exc:
            logger.error("Cannot import fire_alerts: %s — monitor disabled", exc)
            return

    while True:
        try:
            _monitor_cycle(fire_alerts)
        except Exception as exc:
            logger.error("Monitor cycle error: %s", exc)
        time.sleep(POLL_INTERVAL)


def _monitor_cycle(fire_alerts) -> None:
    if not SIGNAL_BUS_PATH.exists():
        return

    import json
    bus = json.loads(SIGNAL_BUS_PATH.read_text())

    # Find all executed positions
    signals     = bus.get("signals", [])
    rts_signals = bus.get("rts_signals", [])
    april_mode  = _get_april_mode(bus)

    executed = [
        s for s in signals
        if str(s.get("december_verdict", "")).upper() == "CONFIRM"
    ]

    if not executed:
        return

    logger.info("Monitor checking %d open position(s) | April=%s", len(executed), april_mode)

    # Fetch live prices for all open pairs
    pairs  = [s.get("pair", "") for s in executed if s.get("pair")]
    prices = _get_current_prices(pairs)

    kills:    List[Dict[str, Any]] = []
    cautions: List[Dict[str, Any]] = []

    # ── April STAND_DOWN overrides everything ─────────────────────────────
    if april_mode == "STAND_DOWN":
        for pos in executed:
            pair = pos.get("pair", "?")
            price = prices.get(pair, 0)
            kill_sig = _build_kill_signal(
                pos,
                reason="APRIL STAND_DOWN — council ordered field pullback",
                kill_type="APRIL_STANDDOWN",
                current_price=price,
            )
            kills.append(kill_sig)
            # Mark position as killed in bus
            pos["december_verdict"] = "KILL"
            pos["killed_at"] = _utc_now()
            pos["kill_reason"] = "APRIL_STAND_DOWN"
        logger.warning("April STAND_DOWN — killing %d position(s)", len(kills))

    else:
        # ── Per-position check ────────────────────────────────────────────
        for pos in executed:
            pair  = pos.get("pair", "?")
            price = prices.get(pair, 0)

            if price == 0:
                logger.warning("No price for %s — skipping", pair)
                continue

            verdict, reason = _check_position(pos, price, rts_signals)

            if verdict == "KILL":
                kill_key = f"{pair}:{pos.get('fired_at','')}"
                if kill_key not in _alerted_kills:
                    _alerted_kills.add(kill_key)
                    kill_sig = _build_kill_signal(pos, reason, "COUNCIL_KILL", price)
                    kills.append(kill_sig)
                    pos["december_verdict"] = "KILL"
                    pos["killed_at"] = _utc_now()
                    pos["kill_reason"] = reason
                    logger.warning("KILL %s — %s", pair, reason)

            elif verdict == "CAUTION":
                caution_sig = _build_kill_signal(pos, reason, "CAUTION", price)
                caution_sig["grade"] = "CAUTION"
                cautions.append(caution_sig)
                logger.info("CAUTION %s — %s", pair, reason)

    # ── Write updated bus ─────────────────────────────────────────────────
    if kills or cautions:
        import json
        SIGNAL_BUS_PATH.write_bytes(
            json.dumps(bus, ensure_ascii=False, indent=2).encode()
        )

    # ── Fire alerts ───────────────────────────────────────────────────────
    if kills:
        logger.info("Firing KILL alerts for %d position(s)", len(kills))
        fire_alerts(kills)

    if cautions:
        # Caution — Pushover + Telegram only, no email
        for sig in cautions:
            try:
                from alerts import _send_pushover, _fmt_pushover, _send_telegram, _fmt_telegram  # type: ignore
                title, body = _fmt_pushover(sig, is_kill=True)
                title = title.replace("KILL", "CAUTION")
                _send_pushover(title, body, priority=0)
                _send_telegram(_fmt_telegram(sig, is_kill=True).replace("KILL SIGNAL", "CAUTION"))
            except Exception as exc:
                logger.warning("Caution alert failed: %s", exc)
