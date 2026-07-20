from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from conviction_scorer import score_v2_shadow
from microstructure import enrich as micro_enrich
from pair_universe import PairUniverse, MarketDataSource
from regime_classifier import RegimeClassifier
from signal_bus import SignalBus
from strategies import ENGINE_CLASSES, score_delta_context


logger = logging.getLogger("RTSSniper")

# ---------------- config ----------------

_SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

REGIME_INTERVAL: Dict[str, int] = {
    "VOLATILE": 15,
    "TREND_UP": 60,
    "TREND_DOWN": 60,
    "RANGE": 240,
    "FEAR": 240,
    "DEAD": 0,
}

RTS_ENGINE_IDS = ["RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE"]

SAMMY_GRADE = "S"
MIN_ALERT_GRADES = {"S", "A"}
BONUS_TRAP_CEILING = 0.40
CAUTION_TRAP_FLOOR = 0.65

QUIET_START_HOUR = 22
QUIET_END_HOUR = 5

# defaults/fallbacks (override via env/config in your app if needed)
FG_URL = "https://api.alternative.me/fng/?limit=1"
TG_TOKEN = ""
TG_CHAT = ""
PO_TOKEN = ""
PO_USER = ""
SIGNAL_TTL_HOURS = 8

OHLC_COLUMNS = ["time", "open", "high", "low", "close", "volume"]


# ---------------- helpers ----------------

def _now_cdt_hour() -> int:
    try:
        from zoneinfo import ZoneInfo
        cdt = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        from datetime import timedelta
        cdt = datetime.now(timezone(timedelta(hours=-5)))
    return cdt.hour


def _is_quiet() -> bool:
    h = _now_cdt_hour()
    return h >= QUIET_START_HOUR or h < QUIET_END_HOUR


def _compute_sizing(entry: Any, sl: Any) -> Dict[str, Any]:
    try:
        rpu = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        rpu = 0.0
    if rpu == 0:
        return {}

    out: Dict[str, Any] = {}
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


def _fetch_fg() -> Dict[str, Any]:
    try:
        r = requests.get(FG_URL, timeout=8)
        d = r.json()["data"][0]
        return {"score": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return {"score": 50, "label": "Neutral"}


_INTENT_WEIGHT = {
    "ATTACK_TRAP": 1.00,
    "ATTACK_BREAK": 0.88,
    "ATTACK": 0.75,
    "PROBE": 0.55,
    "WAIT": 0.30,
    "CUT": 0.10,
    "IGNORE": 0.00,
}


def _rts_score(raw: Dict[str, Any], fg_score: int) -> Dict[str, Any]:
    try:
        offence = float(raw.get("offence_score") or 0.50)
        defence = float(raw.get("defence_score") or 0.50)
        trap_quality = float(raw.get("trap_score") or 0.50)
        intent = str(raw.get("intent", "WAIT"))
        intent_w = _INTENT_WEIGHT.get(intent, 0.30)

        composite = offence * 0.35 + defence * 0.25 + trap_quality * 0.25 + intent_w * 0.15

        if fg_score <= 20:
            composite += 0.05
        elif fg_score >= 80:
            composite -= 0.05

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
            "grade": grade,
            "score": round(composite, 4),
            "tier": tier,
            "rts_offence": round(offence, 3),
            "rts_defence": round(defence, 3),
            "rts_trap_qual": round(trap_quality, 3),
            "rts_intent_w": round(intent_w, 3),
        }
    except Exception as exc:
        logger.warning("_rts_score error: %s", exc)
        return {"grade": "C", "score": 0.50, "tier": "TIER_C"}


def _grade_signal(raw: Dict[str, Any], fg_score: int) -> Dict[str, Any]:
    return _rts_score(raw, fg_score)


# ---------------- sniper ----------------

class RTSSniper:
    def __init__(self, max_pairs: Optional[int] = None) -> None:
        self.universe = PairUniverse(MarketDataSource())
        self.regime_cl = RegimeClassifier()
        self.bus = SignalBus()
        self.max_pairs = max_pairs

    def run(self) -> Dict[str, Any]:
        """Compatibility entrypoint."""
        return self.run_scan()

    def run_scan(self) -> Dict[str, Any]:
        t0 = time.time()
        now = datetime.now(timezone.utc)
        fg = _fetch_fg()
        fg_score = fg["score"]

        logger.info("=== RTS SNIPER cycle start | F&G=%s %s ===", fg_score, fg["label"])

        # PairUniverse in your shared file has no interval/limit API.
        pairs = self.universe.get_active_pairs()
        if self.max_pairs is not None:
            pairs = pairs[: self.max_pairs]

        if not pairs:
            logger.warning("RTS Sniper: no active pairs — aborting cycle")
            return {}

        signals: List[Dict[str, Any]] = []

        for pair_ctx in pairs:
            pair = pair_ctx.symbol
            pair_key = pair_ctx.symbol

            # Minimal frame to satisfy engine/microstructure interfaces.
            price = float(pair_ctx.last_price or 0.0)
            if price <= 0:
                continue

            df = pd.DataFrame(
                [{"open": price, "high": price, "low": price, "close": price, "volume": 0.0}]
                * 60
            )

            regime = "RANGE"
            interval = REGIME_INTERVAL.get(regime, 60)

            try:
                delta_ctx = score_delta_context(df, "LONG")
                delta_score = float(delta_ctx.get("delta_score", 0.0))
            except Exception:
                delta_score = 0.0

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
                    logger.debug("RTS engine %s / %s failed: %s", engine_id, pair, exc)
                    continue

                if raw is None:
                    continue

                raw.setdefault("pair", pair)
                raw.setdefault("engine", engine_id)
                raw.setdefault("regime", regime)
                raw["delta_score"] = delta_score

                try:
                    raw = micro_enrich(raw, df)
                except Exception:
                    pass

                try:
                    v2 = score_v2_shadow(raw, df)
                except Exception:
                    v2 = {}

                graded = _grade_signal(raw, fg_score)
                grade = graded.get("grade", "C")
                if grade not in MIN_ALERT_GRADES:
                    continue

                trap_score = float(raw.get("trap_score", v2.get("trap_risk", 0.5)) or 0.5)
                intent = raw.get("intent", "WAIT")

                rts_caution = trap_score >= CAUTION_TRAP_FLOOR or intent in {"WAIT", "CUT", "IGNORE"}
                action_state = "WAIT" if rts_caution else "CLICK"

                bonus_multiplier = (
                    3.0 if grade == SAMMY_GRADE and trap_score <= BONUS_TRAP_CEILING else 1.0
                )

                signal = self._build_signal(
                    raw=raw,
                    graded=graded,
                    v2=v2,
                    regime=regime,
                    interval=interval,
                    delta_score=delta_score,
                    trap_score=trap_score,
                    intent=intent,
                    action_state=action_state,
                    rts_caution=rts_caution,
                    bonus_multiplier=bonus_multiplier,
                    now=now,
                )
                signals.append(signal)

        _RANK = {
            "ATTACK_TRAP": 0,
            "ATTACK_BREAK": 1,
            "ATTACK": 2,
            "PROBE": 3,
            "WAIT": 4,
            "CUT": 5,
            "IGNORE": 6,
        }
        signals.sort(
            key=lambda sig: (
                _RANK.get(sig.get("intent", "WAIT"), 4),
                -(sig.get("trap_score") or 0),
                -(sig.get("conviction") or 0),
            )
        )

        duration = round(time.time() - t0, 1)
        s_count = sum(1 for s in signals if s["grade"] == SAMMY_GRADE)

        self._write_bus(signals, fg, now, duration)
        self._fire_alerts([s for s in signals if s["grade"] == SAMMY_GRADE])

        logger.info(
            "=== RTS SNIPER done: %d signals | %d S-grade | %.1fs ===",
            len(signals),
            s_count,
            duration,
        )

        return {
            "rts_signals_fired": len(signals),
            "rts_s_grade": s_count,
            "rts_duration_sec": duration,
        }

    def _build_signal(
        self,
        *,
        raw: Dict[str, Any],
        graded: Dict[str, Any],
        v2: Dict[str, Any],
        regime: str,
        interval: int,
        delta_score: float,
        trap_score: float,
        intent: str,
        action_state: str,
        rts_caution: bool,
        bonus_multiplier: float,
        now: datetime,
    ) -> Dict[str, Any]:
        engine = raw.get("engine", "RTS_UNKNOWN")
        return {
            "pair": raw["pair"],
            "bias": raw.get("bias", "LONG"),
            "engine": engine,
            "bot_family": "RTS",
            "setup_class": (
                "TRAP"
                if intent in {"ATTACK_TRAP", "ATTACK_BREAK"} or trap_score >= 0.75
                else "NORMAL"
            ),
            "grade": graded.get("grade", "C"),
            "conviction": graded.get("score", 0.5),
            "feed_eligible": graded.get("grade") in MIN_ALERT_GRADES,
            "rts_family": raw.get("rts_family", engine.replace("RTS_", "")),
            "intent": intent,
            "trap_score": round(trap_score, 3),
            "offence_score": graded.get("rts_offence", raw.get("offence_score")),
            "defence_score": graded.get("rts_defence", raw.get("defence_score")),
            "rts_trap_qual": graded.get("rts_trap_qual"),
            "rts_intent_w": graded.get("rts_intent_w"),
            "action_state": action_state,
            "rts_caution": rts_caution,
            "bonus_multiplier": bonus_multiplier,
            "entry": raw.get("entry"),
            "sl": raw.get("sl"),
            "tp": raw.get("tp"),
            "rr": raw.get("rr"),
            "position_sizing": _compute_sizing(raw.get("entry"), raw.get("sl")),
            "regime": regime,
            "timeframe_min": interval,
            "delta_score": round(delta_score, 3),
            "defensive_score": v2.get("defensive_score"),
            "offensive_score": v2.get("offensive_score"),
            "_v2_trap_risk": v2.get("trap_risk"),
            "_v2_conviction": v2.get("conviction_v2"),
            "_v2_action": v2.get("v2_action"),
            "_v2_reasons": v2.get("v2_reasons", []),
            "fired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=SIGNAL_TTL_HOURS)).isoformat(),
        }

    def _write_bus(
        self,
        signals: List[Dict[str, Any]],
        fg: Dict[str, Any],
        now: datetime,
        duration: float,
    ) -> None:
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

    def _fire_alerts(self, sammys: List[Dict[str, Any]]) -> None:
        quiet = _is_quiet()
        for s in sammys[:5]:
            if quiet:
                logger.info("[RTS SNIPER quiet-hours S] %s %s", s["pair"], s["intent"])

            msg = (
                f"🎯 *RTS SNIPER*\n"
                f"*{s['pair']}* {s['bias']} | {s.get('rts_family','RTS')}\n"
                f"Intent: {s.get('intent')} | Trap: {s.get('trap_score', 0):.2f}\n"
                f"Entry: {s.get('entry')} | SL: {s.get('sl')} | TP: {s.get('tp')}\n"
                f"R:R {s.get('rr', '?')} | Conviction: {s.get('conviction', 0):.2f}"
            )
            _send_telegram(msg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    sniper = RTSSniper(max_pairs=None)
    result = sniper.run()
    print(
        f"RTS Sniper complete: {result.get('rts_signals_fired', 0)} signals | "
        f"{result.get('rts_s_grade', 0)} S-grade | "
        f"{result.get('rts_duration_sec', 0)}s"
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
    sniper.run()
    print(
        f"RTS Sniper complete: {result.get('rts_signals_fired', 0)} signals | "
        f"{result.get('rts_s_grade', 0)} S-grade | "
        f"{result.get('rts_duration_sec', 0)}s"
    )
