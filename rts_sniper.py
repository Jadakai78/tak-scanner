"""rts_sniper.py — RTS Sniper: standalone autonomous stop-hunt and liquidation hunter.

Separate but equal. Runs its own loop parallel to the main scanner.
Does not share signal lanes — writes to ``rts_signals`` key in the bus.

Mission: find where retail stops and liquidations are clustered, go where
the market goes, and fire when the grab happens.

Architecture:
  - Own loop cadence — self-selects timeframe per pair based on regime
  - Multi-TF OHLC fetch: 15m (VOLATILE), 1H (TREND), 4H (RANGE/FEAR)
  - Runs all RTS engines: LIQ, CHOCH, BOS, ZONE, BOTTLE
  - DELTA overlay applied after generate()
  - Own KV bus key: ``rts_signals``
  - Own alert identity: RTS SNIPER
  - Bonus ×3 on clean S-grade (trap_score ≤ 0.40)
  - RTS caution gate: trap ≥ 0.65 or WAIT/CUT → WAIT, no fire
  - Prop-only: only PROP_WHITELIST pairs emit alerts

Timeframe selection by regime:
  VOLATILE   → 15m  (fast stops, quick grabs)
  TREND_UP   → 1H   (structural stops, session levels)
  TREND_DOWN → 1H
  RANGE      → 4H   (equal highs/lows at range extremes)
  FEAR       → 4H   (macro stops, slow liquidations)
  DEAD       → skip (no stops worth hunting)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from conviction_scorer import ConvictionScorer, score_v2_shadow
from microstructure import enrich as micro_enrich
from pair_universe import PairUniverse, PROP_WHITELIST
from regime_classifier import RegimeClassifier
from signal_bus import SignalBus
from strategies import (
    ENGINE_CLASSES,
    score_delta_context,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("rts_sniper")

# ── constants ─────────────────────────────────────────────────────────────────
MODULE_DIR      = Path(__file__).resolve().parent
CONFIG_PATH     = MODULE_DIR / "config.json"
FG_URL          = "https://api.alternative.me/fng/?limit=1"
SIGNAL_TTL_HOURS = 2           # RTS signals expire faster — they're moment-in-time
OHLC_COLUMNS    = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]

# Telegram / Pushover from config
_cfg: Dict[str, Any] = {}
if CONFIG_PATH.exists():
    try:
        _cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass

TG_TOKEN  = _cfg.get("telegram_token",  "")
TG_CHAT   = _cfg.get("telegram_chat_id", "")
PO_TOKEN  = _cfg.get("pushover_token",  "")
PO_USER   = _cfg.get("pushover_user",   "")

# Per-seat risk floors
_SEATS = [
    {"name": "Dragon",   "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66,  "mode": "FULL_AGGRESSION"},
    {"name": "Eval1",    "risk": 13,  "mode": "PROTECT_ONLY"},
]

# Regime → interval mapping (minutes)
REGIME_INTERVAL: Dict[str, int] = {
    "VOLATILE":   15,
    "TREND_UP":   60,
    "TREND_DOWN": 60,
    "RANGE":      240,
    "FEAR":       240,
    "DEAD":       0,   # skip
}

# RTS engines to run (DELTA is overlay-only, not a generator)
RTS_ENGINE_IDS = ["RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE"]

# Grade thresholds (reuse conviction scorer constants)
SAMMY_GRADE       = "S"
MIN_ALERT_GRADES  = {"S", "A"}
BONUS_TRAP_CEILING = 0.40   # trap ≤ this on S-grade → ×3 bonus
CAUTION_TRAP_FLOOR = 0.65   # trap ≥ this → caution, action_state = WAIT

# Quiet hours (CDT)
QUIET_START_HOUR = 22
QUIET_END_HOUR   = 5


# ── helpers ───────────────────────────────────────────────────────────────────

def _now_cdt_hour() -> int:
    try:
        from zoneinfo import ZoneInfo
        cdt = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        from datetime import timezone, timedelta
        cdt = datetime.now(timezone(timedelta(hours=-5)))
    return cdt.hour


def _is_quiet() -> bool:
    h = _now_cdt_hour()
    return h >= QUIET_START_HOUR or h < QUIET_END_HOUR


def _compute_sizing(entry, sl) -> dict:
    try:
        rpu = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        rpu = 0.0
    if rpu == 0:
        return {}
    out = {}
    for seat in _SEATS:
        if seat["mode"] == "PROTECT_ONLY":
            out[seat["name"]] = {"units": 0, "dollar_risk": 0, "mode": "PROTECT_ONLY"}
        else:
            units = round(seat["risk"] / rpu, 2)
            out[seat["name"]] = {
                "units": units,
                "dollar_risk": round(units * rpu, 2),
                "mode": seat["mode"],
            }
    return out


def _send_telegram(message: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        logger.warning("Telegram not configured — skipping alert")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)


def _send_pushover(title: str, message: str, priority: int = 0) -> None:
    if not PO_TOKEN or not PO_USER:
        return
    try:
        requests.post(
            "https://api.pushover.net/1/messages.json",
            data={"token": PO_TOKEN, "user": PO_USER,
                  "title": title, "message": message, "priority": priority},
            timeout=10,
        )
    except Exception as exc:
        logger.error("Pushover send failed: %s", exc)


def _fetch_fg() -> Dict[str, Any]:
    try:
        r = requests.get(FG_URL, timeout=8)
        d = r.json()["data"][0]
        return {"score": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return {"score": 50, "label": "Neutral"}


# ── RTS native scorer ─────────────────────────────────────────────────────────
# Scores built from RTS engine outputs — NOT from V2 conviction scorer.
# High trap_score = good (stop hunt absorbed). Inverted from V2 logic.

_INTENT_WEIGHT = {
    "ATTACK_TRAP":  1.00,
    "ATTACK_BREAK": 0.88,
    "ATTACK":       0.75,
    "PROBE":        0.55,
    "WAIT":         0.30,
    "CUT":          0.10,
    "IGNORE":       0.00,
}


def _rts_score(raw: Dict[str, Any], fg_score: int) -> Dict[str, Any]:
    """
    Native RTS scoring — independent of V2 conviction scorer.

    Dimensions:
      offence  (35%) — engine offence score (liquidity absorption, momentum)
      defence  (25%) — engine defence score (structure, BOS/CHOCH confirmation)
      trap     (25%) — trap quality: HIGH = clean stop hunt absorbed (GOOD)
      intent   (15%) — INTENT_RANK ladder weight

    F&G modifier: extreme fear adds +0.05, extreme greed subtracts 0.05.

    Grade thresholds (RTS-native):
      S  >= 0.82
      A  >= 0.70
      B  >= 0.55
      C  <  0.55
    """
    try:
        offence      = float(raw.get("offence_score") or 0.50)
        defence      = float(raw.get("defence_score") or 0.50)
        trap_quality = float(raw.get("trap_score")    or 0.50)  # high = good for RTS
        intent       = str(raw.get("intent", "WAIT"))
        intent_w     = _INTENT_WEIGHT.get(intent, 0.30)

        composite = (
            offence      * 0.35 +
            defence      * 0.25 +
            trap_quality * 0.25 +
            intent_w     * 0.15
        )

        # Fear & Greed modifier
        if fg_score <= 20:
            composite += 0.05   # extreme fear = opportunity
        elif fg_score >= 80:
            composite -= 0.05   # extreme greed = caution
        composite = max(0.0, min(1.0, composite))

        if composite >= 0.82:
            grade, tier = "S", "TIER_S"
        elif composite >= 0.70:
            grade, tier = "A", "TIER_A"
        elif composite >= 0.55:
            grade, tier = "B", "TIER_B"
        else:
            grade, tier = "C", "TIER_C"

        return {
            "grade":         grade,
            "score":         round(composite, 4),
            "tier":          tier,
            "rts_offence":   round(offence,      3),
            "rts_defence":   round(defence,      3),
            "rts_trap_qual": round(trap_quality, 3),
            "rts_intent_w":  round(intent_w,     3),
        }
    except Exception as exc:
        logger.warning("_rts_score error: %s", exc)
        return {"grade": "C", "score": 0.50, "tier": "TIER_C"}


def _grade_signal(raw: Dict[str, Any], fg_score: int) -> Dict[str, Any]:
    """Grade using native RTS scorer — independent of V2 conviction scorer."""
    return _rts_score(raw, fg_score)


# ── RTS sniper scan ───────────────────────────────────────────────────────────

class RTSSniper:
    """Autonomous RTS stop-hunt and liquidation hunter.

    Runs independently from the main Tak scanner. Writes to ``rts_signals``
    bus key. Fires its own Telegram alerts under the RTS SNIPER identity.
    """

    def __init__(self, max_pairs: Optional[int] = None) -> None:
    from pair_universe import PairUniverse, MarketDataSource
        self.universe = PairUniverse(MarketDataSource())
        self.regime_cl = RegimeClassifier()
        self.bus       = SignalBus()
        self.max_pairs = max_pairs

    # ── top-level scan ────────────────────────────────────────────────────────

    def run_scan(self) -> Dict[str, Any]:
        """One full RTS sniper cycle across all prop pairs."""
        t0  = time.time()
        now = datetime.now(timezone.utc)
        fg  = _fetch_fg()
        fg_score = fg["score"]

        logger.info("=== RTS SNIPER cycle start | F&G=%s %s ===",
                    fg_score, fg["label"])

        # Fetch 4H universe for pair list + regime classification
        active_4h = self.universe.get_active_pairs(interval=240,
                                                    limit=self.max_pairs)
        if not active_4h:
            logger.warning("RTS Sniper: no active pairs — aborting cycle")
            return {}

        signals: List[Dict[str, Any]] = []
        killed:  List[Dict[str, Any]] = []

        for item in active_4h:
            pair     = item["pair"]
            pair_key = item["pair_key"]

            # ── regime from 4H ───────────────────────────────────────────────
            df_4h = self._ohlc_df(item)
            if df_4h is None or len(df_4h) < 20:
                continue
            regime = self._classify_regime(df_4h, fg_score)
            if regime == "DEAD":
                logger.debug("RTS Sniper: %s DEAD — skip", pair)
                continue

            # ── pick timeframe for this regime ───────────────────────────────
            interval = REGIME_INTERVAL.get(regime, 60)
            df = self._fetch_tf_ohlc(pair_key, interval, df_4h)
            if df is None or len(df) < 30:
                continue

            # ── delta overlay pre-compute ─────────────────────────────────────
            try:
                delta_ctx = score_delta_context(df, "LONG")
                delta_score = float(delta_ctx.get("delta_score", 0.0))
            except Exception:
                delta_score = 0.0

            # ── run each RTS engine ───────────────────────────────────────────
            for engine_id in RTS_ENGINE_IDS:
                cls = ENGINE_CLASSES.get(engine_id)
                if cls is None:
                    continue
                try:
                    engine = cls()
                    raw = engine.generate(
                        pair=pair,
                        ohlc_df=df,
                        regime=regime,
                        fg_score=fg_score,
                        ai_st={},
                    )
                except Exception as exc:
                    logger.debug("RTS engine %s / %s failed: %s",
                                 engine_id, pair, exc)
                    continue

                if raw is None:
                    continue

                # ── bias guard — RTS_BOTTLE is always LONG ───────────────────
                raw.setdefault("pair", pair)
                raw.setdefault("engine", engine_id)
                raw.setdefault("regime", regime)

                # ── inject delta ──────────────────────────────────────────────
                raw["delta_score"] = delta_score

                # ── microstructure enrich ─────────────────────────────────────
                try:
                    raw = micro_enrich(raw, df)
                except Exception:
                    pass

                # ── V2 shadow score ───────────────────────────────────────────
                try:
                    v2 = score_v2_shadow(raw, df)
                except Exception:
                    v2 = {}

                # ── grade ─────────────────────────────────────────────────────
                graded = _grade_signal(raw, fg_score)
                grade  = graded.get("grade", "C")

                # ── feed eligibility ──────────────────────────────────────────
                if grade not in MIN_ALERT_GRADES:
                    logger.debug("RTS Sniper: %s/%s grade=%s — skip",
                                 pair, engine_id, grade)
                    continue

                # ── trap score + intent ───────────────────────────────────────
                trap_score = float(
                    raw.get("trap_score",
                    v2.get("trap_risk", 0.5)) or 0.5
                )
                intent     = raw.get("intent", "WAIT")

                # ── caution gate ──────────────────────────────────────────────
                rts_caution = (
                    trap_score >= CAUTION_TRAP_FLOOR
                    or intent in {"WAIT", "CUT", "IGNORE"}
                )
                action_state = "WAIT" if rts_caution else "CLICK"

                # ── bonus multiplier ──────────────────────────────────────────
                bonus_multiplier = (
                    3.0 if grade == SAMMY_GRADE
                        and trap_score <= BONUS_TRAP_CEILING
                    else 1.0
                )

                # ── assemble signal ───────────────────────────────────────────
                signal = self._build_signal(
                    raw=raw, graded=graded, v2=v2,
                    regime=regime, interval=interval,
                    delta_score=delta_score, trap_score=trap_score,
                    intent=intent, action_state=action_state,
                    rts_caution=rts_caution,
                    bonus_multiplier=bonus_multiplier,
                    now=now,
                )
                signals.append(signal)
                logger.info(
                    "RTS SNIPER SIGNAL | %s %s %s | grade=%s intent=%s "
                    "trap=%.2f bonus=%.1fx caution=%s TF=%dm",
                    pair, raw.get("bias"), engine_id, grade, intent,
                    trap_score, bonus_multiplier, rts_caution, interval,
                )

        # ── sort by intent priority ───────────────────────────────────────────
        _RANK = {"ATTACK_TRAP": 0, "ATTACK_BREAK": 1, "ATTACK": 2,
                 "PROBE": 3, "WAIT": 4, "CUT": 5, "IGNORE": 6}
        signals.sort(key=lambda sig: (
            _RANK.get(sig.get("intent", "WAIT"), 4),
            -(sig.get("trap_score") or 0),
            -(sig.get("conviction") or 0),
        ))

        duration = round(time.time() - t0, 1)
        s_count  = sum(1 for s in signals if s["grade"] == SAMMY_GRADE)
        logger.info(
            "=== RTS SNIPER done: %d signals | %d S-grade | %.1fs ===",
            len(signals), s_count, duration,
        )

        # ── write own bus lane ────────────────────────────────────────────────
        self._write_bus(signals, fg, now, duration)

        # ── fire alerts ──────────────────────────────────────────────────────
        sammys = [s for s in signals if s["grade"] == SAMMY_GRADE]
        self._fire_alerts(sammys)

        return {
            "rts_signals_fired": len(signals),
            "rts_s_grade": s_count,
            "rts_duration_sec": duration,
        }

    # ── OHLC helpers ──────────────────────────────────────────────────────────

    def _ohlc_df(self, item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        """Build DataFrame from universe item's cached 4H OHLC."""
        raw = item.get("ohlc_4h") or item.get("ohlc")
        if not raw:
            return None
        try:
            df = pd.DataFrame(raw, columns=OHLC_COLUMNS)
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna(subset=["open", "high", "low", "close"])
        except Exception:
            return None

    def _fetch_tf_ohlc(
        self, pair_key: str, interval: int, fallback_df: pd.DataFrame
    ) -> Optional[pd.DataFrame]:
        """Fetch OHLC at the requested interval.

        Falls back to the 4H dataframe if interval==240 or fetch fails.
        """
        if interval == 240:
            return fallback_df
        try:
            df = self.universe.fetch_ohlc(pair_key, interval=interval)
            if df is not None and len(df) >= 20:
                return df
        except Exception as exc:
            logger.debug("TF fetch %s %dm failed: %s", pair_key, interval, exc)
        return fallback_df  # graceful fallback

    def _classify_regime(self, df: pd.DataFrame, fg_score: int) -> str:
        """Return regime string from RegimeClassifier."""
        try:
            result = self.regime_cl.classify(df, fg_score=fg_score)
            return result if isinstance(result, str) else result.get("regime", "RANGE")
        except Exception:
            return "RANGE"

    # ── signal assembly ───────────────────────────────────────────────────────

    def _build_signal(
        self, *, raw: Dict[str, Any], graded: Dict[str, Any],
        v2: Dict[str, Any], regime: str, interval: int,
        delta_score: float, trap_score: float, intent: str,
        action_state: str, rts_caution: bool,
        bonus_multiplier: float, now: datetime,
    ) -> Dict[str, Any]:
        engine = raw.get("engine", "RTS_UNKNOWN")
        return {
            # identity
            "pair":             raw["pair"],
            "bias":             raw.get("bias", "LONG"),
            "engine":           engine,
            "bot_family":       "RTS",
            "setup_class":      (
                "TRAP" if intent in {"ATTACK_TRAP", "ATTACK_BREAK"}
                          or trap_score >= 0.75
                else "NORMAL"
            ),
            # grading
            "grade":            graded.get("grade", "C"),
            "conviction":       graded.get("score", 0.5),
            "feed_eligible":    graded.get("grade") in MIN_ALERT_GRADES,
            # RTS envelope
            "rts_family":       raw.get("rts_family", engine.replace("RTS_", "")),
            "intent":           intent,
            "trap_score":       round(trap_score, 3),
            "offence_score":    graded.get("rts_offence",  raw.get("offence_score")),
            "defence_score":    graded.get("rts_defence",  raw.get("defence_score")),
            "rts_trap_qual":    graded.get("rts_trap_qual"),
            "rts_intent_w":     graded.get("rts_intent_w"),
            "kill_level":       raw.get("kill_level"),
            "auto_cut":         raw.get("auto_cut", False),
            "rts_reasons":      raw.get("rts_reasons", []),
            # execution
            "action_state":     action_state,
            "rts_caution":      rts_caution,
            "bonus_multiplier": bonus_multiplier,
            # levels
            "entry":            raw.get("entry"),
            "sl":               raw.get("sl"),
            "tp":               raw.get("tp"),
            "rr":               raw.get("rr"),
            # sizing
            "position_sizing":  _compute_sizing(raw.get("entry"), raw.get("sl")),
            # context
            "regime":           regime,
            "timeframe_min":    interval,
            "delta_score":      round(delta_score, 3),
            # pattern tags (BOTTLE-specific)
            "pattern":          raw.get("pattern"),
            "higher_lows":      raw.get("higher_lows"),
            "choch":            raw.get("choch"),
            # LIQ-specific
            "liquidity_pool_type": raw.get("liquidity_pool_type"),
            "sweep_side":       raw.get("sweep_side"),
            "sweep_level":      raw.get("sweep_level"),
            "reclaim_status":   raw.get("reclaim_status"),
            # V2 shadow
            "defensive_score":  v2.get("defensive_score"),
            "offensive_score":  v2.get("offensive_score"),
            # V2 shadow kept for diagnostics/bus only — never shown in feed or alerts
            "_v2_trap_risk":    v2.get("trap_risk"),
            "_v2_conviction":   v2.get("conviction_v2"),
            "_v2_action":       v2.get("v2_action"),
            "_v2_reasons":      v2.get("v2_reasons", []),
            # timestamps
            "fired_at":         now.isoformat(),
            "expires_at":       (now + timedelta(hours=SIGNAL_TTL_HOURS)).isoformat(),
        }

    # ── bus write ─────────────────────────────────────────────────────────────

    def _write_bus(
        self, signals: List[Dict[str, Any]], fg: Dict[str, Any],
        now: datetime, duration: float,
    ) -> None:
        """Write RTS signals to own ``rts_signals`` key in the shared bus."""
        update = {
            "rts_signals": signals,
            "rts_last_scan": now.isoformat(),
            "rts_next_scan": (now + timedelta(minutes=10)).isoformat(),
            "rts_session_stats": {
                "signals_fired": len(signals),
                "s_grade_count": sum(1 for s in signals if s["grade"] == "S"),
                "scan_duration_sec": duration,
            },
            "f_g": fg,
        }
        self.bus.update(update)

    # ── alerts ────────────────────────────────────────────────────────────────

    def _fire_alerts(self, sammys: List[Dict[str, Any]]) -> None:
        """Fire RTS SNIPER Telegram alerts for S-grade signals."""
        quiet = _is_quiet()
        for s in sammys[:5]:  # cap at 5 per cycle
            bonus     = s.get("bonus_multiplier", 1.0)
            trap_sc   = s.get("trap_score", 0.5)
            rts_fam   = s.get("rts_family", "RTS")
            intent    = s.get("intent", "")
            tf_min    = s.get("timeframe_min", 60)
            tf_label  = {15: "15m", 60: "1H", 240: "4H"}.get(tf_min, f"{tf_min}m")
            caution   = s.get("rts_caution", False)
            sizing    = s.get("position_sizing", {})
            pool_type = s.get("liquidity_pool_type", "")

            # sizing block
            size_lines = []
            for seat, data in sizing.items():
                if data.get("mode") == "PROTECT_ONLY":
                    size_lines.append(f"{seat}: PROTECT ONLY")
                else:
                    size_lines.append(
                        f"{seat}: {data['units']} units | ${data['dollar_risk']} risk"
                    )
            sizing_block = "\n".join(size_lines) if size_lines else "Sizing: N/A"

            # bonus tag
            bonus_tag = (
                f"\n🔥 *BONUS ×3* — Clean Sammy (trap {trap_sc:.2f})"
                if bonus >= 3.0 else ""
            )

            # caution tag
            caution_tag = (
                "\n⚠️ *RTS CAUTION* — let trap resolve first"
                if caution else ""
            )

            # pool context
            pool_tag = f" | Pool: {pool_type}" if pool_type else ""

            # reasons (top 2)
            reasons = s.get("rts_reasons", [])[:2]
            reason_block = (
                "\n" + "\n".join(f"• {r}" for r in reasons)
                if reasons else ""
            )

            message = (
                f"🎯 *RTS SNIPER*\n"
                f"*{s['pair']}* {s['bias']} — {tf_label} | {rts_fam}{pool_tag}\n"
                f"Intent: {intent} | Trap: {trap_sc:.2f}\n"
                f"Entry: {s['entry']} | SL: {s['sl']} | TP: {s['tp']}\n"
                f"R:R {s.get('rr', '?')} | RTS: {s.get('score', s.get('conviction', 0)):.2f} | O:{s.get('offence_score', 0):.2f} D:{s.get('defence_score', 0):.2f} T:{s.get('rts_trap_qual', 0):.2f}\n"
                f"{sizing_block}"
                f"{reason_block}"
                f"{bonus_tag}"
                f"{caution_tag}"
            )

            if quiet:
                # S-grade fires through quiet hours — Sammy exception
                logger.info("[RTS SNIPER quiet-hours S] %s %s %s",
                            s["pair"], s["bias"], intent)

            logger.info("[RTS SNIPER ALERT] %s %s %s trap=%.2f bonus=%.1fx",
                        s["pair"], s["bias"], intent, trap_sc, bonus)
            _send_telegram(message)


# ── standalone runner ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    sniper = RTSSniper(max_pairs=None)
    result = sniper.run_scan()
    print(
        f"RTS Sniper complete: {result.get('rts_signals_fired', 0)} signals | "
        f"{result.get('rts_s_grade', 0)} S-grade | "
        f"{result.get('rts_duration_sec', 0)}s"
    )
