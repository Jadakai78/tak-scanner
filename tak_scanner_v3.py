"""tak_scanner_v3.py — Tak scanner v3, the main orchestrator (Layer 2).

Wires the whole pipeline together for one scan cycle:

    F&G -> PairUniverse -> per pair {RegimeClassifier, AISupertrend} ->
    eligible strategy engines -> ConvictionScorer (+ S8 MTF multiplier) ->
    Remi kill protocol -> SignalBus write.

Runs on Windows Task Scheduler (see README). Grade F is discarded; only
S-grade CLEAN signals are Sammy-eligible. Quiet hours (10PM-5AM CDT) suppress
alerts but the scan still runs and S-grade still fires.
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

from ai_supertrend import AISupertrend
from conviction_scorer import ConvictionScorer, score_v2_shadow
from microstructure import enrich as micro_enrich
from pair_universe import PairUniverse, PROP_WHITELIST
from regime_classifier import RegimeClassifier
from remi import Remi
from signal_bus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES, S8MTFConfluence, score_delta_context

# Per-seat risk floors (dollars) — mirrors casino_counter.py FIXED_MIN_RISK
_SEATS = [
    {"name": "Dragon",   "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66,  "mode": "FULL_AGGRESSION"},
    {"name": "Eval1",    "risk": 13,  "mode": "PROTECT_ONLY"},
]


def _compute_sizing(entry, sl) -> dict:
    """Return per-seat unit counts and dollar risk given entry and stop-loss."""
    try:
        risk_per_unit = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        risk_per_unit = 0.0
    if risk_per_unit == 0:
        return {}
    sizing = {}
    for seat in _SEATS:
        if seat["mode"] == "PROTECT_ONLY":
            sizing[seat["name"]] = {"units": 0, "dollar_risk": 0, "mode": "PROTECT_ONLY"}
        else:
            units = round(seat["risk"] / risk_per_unit, 2)
            sizing[seat["name"]] = {
                "units": units,
                "dollar_risk": round(units * risk_per_unit, 2),
                "mode": seat["mode"],
            }
    return sizing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tak_scanner_v3")

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "config.json"
FG_URL = "https://api.alternative.me/fng/?limit=1"

MAX_SAMMY_ALERTS = 5           # Rule 9 (Tier 1)
SIGNAL_TTL_HOURS = 4
OHLC_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]

# Tier-1 (4H) scan schedule: :45 past 3,7,11,15,19,23 UTC.
SCAN_HOURS_UTC = [3, 7, 11, 15, 19, 23]
SCAN_MINUTE_UTC = 45

# Pairs eligible for Tier A / Tier B tagging on S-grade alerts (ATR% ranked at
# scan time; this is the full candidate set considered for the top-5 cut).
TIER_TAG_CANDIDATES = [
    "SOLUSD", "XRPUSD", "BTCUSD", "ETHUSD", "ADAUSD", "AVAXUSD", "LINKUSD",
    "DOTUSD", "AAVEUSD", "DOGEUSD",
]


def send_sammy(message: str) -> None:
    """Send a Telegram alert to Sammy (fire-and-forget, never raises).

    Args:
        message: Markdown-formatted alert text.
    """
    TOKEN = "8860741830:AAGiccCbk4dzoTq97gWIIykZVunDvkkl6ys"
    CHAT_ID = "7733126931"
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                     json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
                     timeout=10)
    except Exception as e:
        logging.warning(f"Sammy alert failed: {e}")


def _load_sprint_mode() -> bool:
    """Read the sprint_mode flag from config.json (defaults to False)."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return bool(cfg.get("sprint_mode", False))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read sprint_mode from config.json (%s) — defaulting False.", exc)
        return False


def _tier_for_pair(pair: str, active_pairs: List[Dict[str, Any]]) -> str:
    """Tag a pair TIER_A if it ranks in the top 5 by ATR%% among active pairs.

    Args:
        pair: Pair base symbol (e.g. "SOL").
        active_pairs: The scanned universe items (each with 'pair'/'atr_pct').

    Returns:
        "TIER_A" or "TIER_B".
    """
    ranked = sorted(
        (item for item in active_pairs if "atr_pct" in item),
        key=lambda item: item.get("atr_pct", 0.0),
        reverse=True,
    )
    top5 = {item["pair"] for item in ranked[:5]}
    return "TIER_A" if pair in top5 else "TIER_B"


def _now_cdt_hour() -> int:
    """Current hour in America/Chicago (CDT/CST), stdlib-first."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago")).hour
    except Exception:  # noqa: BLE001 - fall back to a fixed UTC-5 offset
        return (datetime.now(timezone.utc) - timedelta(hours=5)).hour


class TakScannerV3:
    """Full scan orchestrator.

    Attributes:
        max_pairs: Cap on pairs analyzed per scan (None = all active).
    """

    def __init__(self, max_pairs: Optional[int] = None) -> None:
        """Initialize the scanner and all sub-components.

        Args:
            max_pairs: Optional cap on the number of pairs analyzed (keeps a
                manual run rate-limit friendly). ``None`` scans all.
        """
        self.max_pairs = max_pairs
        self.universe = PairUniverse()
        self.regime = RegimeClassifier()
        self.ai_st = AISupertrend()
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        self.bus = SignalBus()
        self.s8 = S8MTFConfluence(
            fetch_ohlc=self.universe.fetch_ohlc, ai_supertrend=self.ai_st
        )

    # ------------------------------------------------------------------
    def fetch_fg(self) -> Dict[str, Any]:
        """Fetch the current Fear & Greed reading.

        Returns:
            ``{score:int, label:str}`` (neutral 50 fallback on failure).
        """
        try:
            resp = self.universe.session.get(FG_URL, timeout=10)
            resp.raise_for_status()
            d = resp.json()["data"][0]
            return {"score": int(d["value"]), "label": d["value_classification"]}
        except Exception as exc:  # noqa: BLE001
            logger.warning("F&G fetch failed (%s) — using neutral 50.", exc)
            return {"score": 50, "label": "Neutral"}

    @staticmethod
    def _next_scan_time(now: datetime) -> datetime:
        """Compute the next Tier-1 scan timestamp (UTC)."""
        candidates = []
        for h in SCAN_HOURS_UTC:
            t = now.replace(hour=h, minute=SCAN_MINUTE_UTC, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            candidates.append(t)
        return min(candidates)

    @staticmethod
    def _df_from_universe(item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        """Rebuild an OHLC DataFrame from a universe item's raw ``ohlc_4h``."""
        raw = item.get("ohlc_4h")
        if not raw:
            return None
        try:
            df = pd.DataFrame(raw, columns=OHLC_COLUMNS)
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna().reset_index(drop=True)
        except (ValueError, KeyError):
            return None

    # ------------------------------------------------------------------
    def run_scan(self) -> Dict[str, Any]:
        """Execute one full scan cycle and write the signal bus.

        Returns:
            The session_stats dict for this scan.
        """
        start = time.time()
        now = datetime.now(timezone.utc)
        quiet = _is_quiet_hours()
        sprint_mode = _load_sprint_mode()
        fg = self.fetch_fg()
        fg_score = fg["score"]
        logger.info("Scan start | F&G=%s (%s) | quiet_hours=%s | sprint_mode=%s",
                    fg_score, fg["label"], quiet, sprint_mode)

        active = self.universe.get_active_pairs(interval=240, limit=self.max_pairs)
        logger.info("Active universe: %d pairs", len(active))

        signals: List[Dict[str, Any]] = []
        killed: List[Dict[str, Any]] = []
        regime_map: Dict[str, str] = {}
        dead_count = 0

        for item in active:
            pair = item["pair"]
            df = self._df_from_universe(item)
            if df is None or len(df) < 60:
                continue

            regime = self.regime.classify(pair, df, fg_score)
            regime_map[pair] = regime
            if regime == "DEAD":
                dead_count += 1
                continue

            ai_st = self.ai_st.compute(pair, df)
            daily_df: Optional[pd.DataFrame] = None  # lazily fetched for Remi/S8

            for engine_id in REGIME_ENGINES.get(regime, []):
                raw = self._run_engine(engine_id, pair, df, regime, fg_score, ai_st)
                if raw is None:
                    continue
                if not raw.get("bias"):
                    continue  # engine returned incomplete raw — skip safely

                # Enrich with AI-ST + MTF before scoring.
                raw["ai_st_direction"] = ai_st["direction"]
                raw["ai_st_strength"] = ai_st["signal_strength"]
                mtf = self.s8.score_mtf(pair, raw["bias"], df, pair_key=item["pair_key"])
                raw["mtf_alignment"] = mtf["mtf_verdict"]
                # V2 Phase 1: attach microstructure raw fields (non-destructive)
                raw["atr_pct"] = item.get("atr_pct", 0.0)
                raw["volume_ratio"] = item.get("volume_ratio", 1.0)
                micro_enrich(raw, df, active)

                # RTS-DELTA overlay: attach sponsorship context to all signals
                if raw.get("engine", "").startswith("RTS"):
                    delta_ctx = score_delta_context(df, raw.get("bias", "LONG"))
                    raw.update(delta_ctx)
                    # Apply delta modifier to offence_score
                    if "offence_score" in raw:
                        raw["offence_score"] = min(1.0, max(0.0,
                            raw["offence_score"] + delta_ctx.get("delta_modifier", 0.0)
                        ))

                graded = self.scorer.score(raw)
                # V2 Phase 1: compute shadow scores
                v2 = score_v2_shadow(raw)
                if graded["grade"] == "F":
                    continue

                # Remi kill protocol (needs daily OHLC).
                if daily_df is None:
                    daily_df = self.universe.fetch_ohlc(item["pair_key"], interval=1440)
                verdict = self.remi.evaluate(
                    {**raw, "conviction": graded["score"]}, daily_df, fg_score
                )

                # RTS resolver — full envelope for every signal
                rts = self._resolve_rts(raw, graded, mtf, fg_score)
                tier = _tier_for_pair(pair, active)
                action_state = self._derive_action_state(rts["intent"], tier)

                enriched = self._finalize_signal(
                    raw, graded, mtf, ai_st, verdict, now, active, v2, rts, action_state
                )
                if verdict["status"] == "KILLED" or self._should_cut_now(enriched):
                    killed.append({
                        "pair": pair, "engine": engine_id, "bias": raw["bias"],
                        "kill_reason": verdict["reason"] or rts["intent"],
                        "killed_at": now.isoformat(),
                        "rts_family": rts["rts_family"],
                        "intent": rts["intent"],
                    })
                else:
                    # Prop-only gate — only publish signals for whitelisted pairs
                    if pair in PROP_WHITELIST:
                        signals.append(enriched)


        _INTENT_RANK = {
            "ATTACK_TRAP": 0, "ATTACK_BREAK": 1, "ATTACK": 2,
            "PROBE": 3, "WAIT": 4, "CUT": 5, "IGNORE": 6,
        }
        signals.sort(key=lambda sig: (
            _INTENT_RANK.get(sig.get("intent", "WAIT"), 4),
            -(sig.get("defence_score") or 0.0),
            -(sig.get("trap_score")    or 0.0),
            -(sig.get("offence_score") or 0.0),
            -(sig.get("conviction")    or 0.0),
        ))
        sammy = [s for s in signals if s["grade"] in ("S", "A")][:MAX_SAMMY_ALERTS]

        stats = {
            "signals_fired": len(signals),
            "signals_killed": len(killed),
            "s_grade_count": len(sammy),
            "scan_duration_sec": round(time.time() - start, 2),
        }
        self.bus.update({
            "last_scan": now.isoformat(),
            "next_scan": self._next_scan_time(now).isoformat(),
            "f_g": fg,
            "active_pairs": len(active),
            "dead_pairs": dead_count,
            "signals": signals,
            "killed_signals": killed,
            "regime_map": regime_map,
            "session_stats": stats,
            "quiet_hours": quiet,
            "sprint_mode": sprint_mode,
        })
        self._fire_alerts(sammy, quiet, sprint_mode)
        logger.info("Scan complete: %d fired, %d killed, %d S-grade in %.1fs",
                    stats["signals_fired"], stats["signals_killed"],
                    stats["s_grade_count"], stats["scan_duration_sec"])
        return stats

    # ------------------------------------------------------------------
    def _run_engine(
        self, engine_id: str, pair: str, df: pd.DataFrame,
        regime: str, fg_score: int, ai_st: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Instantiate and run a single engine, guarding against exceptions."""
        cls = ENGINE_CLASSES.get(engine_id)
        if cls is None:
            return None
        try:
            return cls().generate(pair, df, regime, fg_score, ai_st=ai_st)
        except Exception as exc:  # noqa: BLE001 - one engine must not kill the scan
            logger.warning("Engine %s failed on %s: %s", engine_id, pair, exc)
            return None

    @staticmethod
    def _finalize_signal(
        raw: Dict[str, Any], graded: Dict[str, Any], mtf: Dict[str, Any],
        ai_st: Dict[str, Any], verdict: Dict[str, Any], now: datetime,
        active_pairs: Optional[List[Dict[str, Any]]] = None,
        v2: Optional[Dict[str, Any]] = None,
        rts: Optional[Dict[str, Any]] = None,
        action_state: str = "WAIT",
    ) -> Dict[str, Any]:
        """Assemble the full bus-schema signal record (raw OHLC stripped)."""
        v2 = v2 or {}
        rts = rts or {}
        return {
            "pair": raw["pair"],
            "bias": raw["bias"],
            "engine": raw["engine"],
            "grade": graded["grade"],
            "conviction": graded["score"],
            # RTS primary action surface
            "action_state": action_state,
            "ai_st_direction": ai_st["direction"],
            "ai_st_strength": ai_st["signal_strength"],
            "mtf_verdict": mtf["mtf_verdict"],
            "mtf_score": mtf["mtf_score"],
            "entry": raw["entry"],
            "sl": raw["sl"],
            "tp": raw["tp"],
            "rr": raw["rr"],
            "regime": raw["regime"],
            "structure_quality": raw.get("structure_quality"),
            "remi_status": verdict["status"],
            "remi_caution": verdict["caution"],
            "kill_reason": verdict["reason"],
            "december_verdict": "PENDING",
            "tier": _tier_for_pair(raw["pair"], active_pairs or []),
            "fired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=SIGNAL_TTL_HOURS)).isoformat(),
            "position_sizing": _compute_sizing(raw.get("entry"), raw.get("sl")),
            # V2 shadow fields (Phase 1 — non-destructive)
            "defensive_score": v2.get("defensive_score"),
            "offensive_score": v2.get("offensive_score"),
            "trap_risk": v2.get("trap_risk"),
            "conviction_v2": v2.get("conviction_v2"),
            "v2_action": v2.get("v2_action"),
            "v2_reasons": v2.get("v2_reasons", []),
            # V2 sub-scores for display
            "v2_sweep_quality": v2.get("v2_sweep_quality"),
            "v2_stop_hunt_recovery": v2.get("v2_stop_hunt_recovery"),
            "v2_absorption": v2.get("v2_absorption"),
            "v2_displacement": v2.get("v2_displacement"),
            "v2_path": v2.get("v2_path"),
            "v2_compression": v2.get("v2_compression"),
            "v2_leadership": v2.get("v2_leadership"),
            # Microstructure raw fields
            "sweep_detected": raw.get("sweep_detected"),
            "sweep_depth": raw.get("sweep_depth"),
            "reclaim_close_ratio": raw.get("reclaim_close_ratio"),
            "acceptance_bars": raw.get("acceptance_bars"),
            "displacement_quality": raw.get("displacement_quality"),
            "inefficiency_path": raw.get("inefficiency_path"),
            "compression_ratio": raw.get("compression_ratio"),
            "relative_leadership": raw.get("relative_leadership"),
            "liquidation_cluster_distance": raw.get("liquidation_cluster_distance"),
            # RTS shared envelope (from resolver — authoritative)
            "rts_family": rts.get("rts_family", raw.get("rts_family")),
            "intent": rts.get("intent", raw.get("intent")),
            "kill_level": rts.get("kill_level", raw.get("kill_level")),
            "auto_cut": rts.get("auto_cut", raw.get("auto_cut", False)),
            "rts_reasons": rts.get("rts_reasons", []),
            "offence_score": rts.get("offence_score", raw.get("offence_score")),
            "defence_score": rts.get("defence_score", raw.get("defence_score")),
            "trap_score": rts.get("trap_score", raw.get("trap_score")),
            # RTS-LIQ specific
            "liquidity_pool_type": raw.get("liquidity_pool_type"),
            "sweep_side": raw.get("sweep_side"),
            "sweep_level": raw.get("sweep_level"),
            "sweep_type": raw.get("sweep_type"),
            "reclaim_status": raw.get("reclaim_status"),
            "continuation_status": raw.get("continuation_status"),
            # RTS-CHOCH specific
            "choch_direction": raw.get("choch_direction"),
            "choch_level": raw.get("choch_level"),
            "flip_confirmed": raw.get("flip_confirmed"),
            # RTS-BOS specific
            "bos_level": raw.get("bos_level"),
            "bos_direction": raw.get("bos_direction"),
            "bos_retest_valid": raw.get("bos_retest_valid"),
            # RTS-ZONE specific
            "zone_top": raw.get("zone_top"),
            "zone_bottom": raw.get("zone_bottom"),
            "zone_touches": raw.get("zone_touches"),
            "zone_mitigated": raw.get("zone_mitigated"),
            # RTS-DELTA overlay
            "delta_bias": raw.get("delta_bias"),
            "sponsorship_quality": raw.get("sponsorship_quality"),
            "vp_context": raw.get("vp_context"),
            "vpoc": raw.get("vpoc"),
        }


    @staticmethod
    def _resolve_rts(raw: Dict[str, Any], graded: Dict[str, Any],
                     mtf: Dict[str, Any], fg_score: int) -> Dict[str, Any]:
        """Infer RTS family, normalize scores, derive intent, kill_level, auto_cut.

        For native RTS engines the fields are already populated — this resolver
        normalises them and fills gaps for legacy S1-S9 signals so every signal
        carries a complete RTS envelope.
        """
        engine = raw.get("engine", "")
        is_rts = engine.startswith("RTS")

        # ── family inference ─────────────────────────────────────────────────
        if is_rts:
            rts_family = raw.get("rts_family", engine.replace("RTS_", ""))
        else:
            # Legacy engines: infer family from score composition
            components = raw.get("score_components", {})
            sweep = raw.get("sweep_detected", False)
            struct = raw.get("structure_quality", 0.5)
            if sweep:
                rts_family = "LIQ"
            elif struct >= 0.65:
                rts_family = "BOS"
            else:
                rts_family = "ZONE"

        # ── score normalisation ───────────────────────────────────────────────
        if is_rts:
            offence = float(raw.get("offence_score") or 0.5)
            defence = float(raw.get("defence_score") or 0.5)
            trap    = float(raw.get("trap_score")    or 0.5)
        else:
            # Derive from existing V1 / V2 shadow fields
            conv  = float(graded.get("score", 0.5))
            sq    = float(raw.get("structure_quality") or 0.5)
            mtf_v = {"FULL": 1.0, "PARTIAL": 0.75, "NONE": 0.5}.get(
                        raw.get("mtf_alignment", "NONE"), 0.5)
            sweep_q = float(raw.get("displacement_quality") or sq)
            offence = min(1.0, conv * 0.60 + sweep_q * 0.20 + mtf_v * 0.20)
            defence = min(1.0, sq * 0.50 + mtf_v * 0.30 + 0.20)
            trap    = float(raw.get("sweep_depth") or 0.0) * 0.5 + sq * 0.5

        # Delta sponsorship modifier (applied by delta overlay already for RTS)
        delta_mod = float(raw.get("delta_modifier") or 0.0)
        offence = min(1.0, max(0.0, offence + delta_mod))

        # ── intent derivation ─────────────────────────────────────────────────
        if is_rts:
            intent = raw.get("intent", "WAIT")
        else:
            grade = graded.get("grade", "F")
            if grade == "S":
                intent = "ATTACK_TRAP" if trap >= 0.60 else "ATTACK_BREAK"
            elif grade == "A":
                intent = "ATTACK_BREAK" if offence >= 0.68 else "PROBE"
            elif grade in ("B", "C"):
                intent = "PROBE" if offence >= 0.55 else "WAIT"
            else:
                intent = "IGNORE"

        # REMI caution → downgrade intent but never upgrade past engine output
        caution = raw.get("remi_caution", "")
        if caution and intent in ("ATTACK_TRAP", "ATTACK_BREAK", "ATTACK"):
            intent = "PROBE"

        # ── kill level ────────────────────────────────────────────────────────
        kill_level = raw.get("kill_level")
        if kill_level is None:
            kill_level = raw.get("sl")   # SL is the mechanical kill for legacy

        # ── auto_cut ─────────────────────────────────────────────────────────
        auto_cut = bool(raw.get("auto_cut", False))

        # ── rts_reasons ───────────────────────────────────────────────────────
        reasons: List[str] = []
        if raw.get("reclaim_status") == "RECLAIMED":
            reasons.append("reclaimed")
        if raw.get("sweep_detected"):
            reasons.append("sweep confirmed")
        if raw.get("flip_confirmed"):
            reasons.append("CHOCH flip")
        if raw.get("bos_retest_valid"):
            reasons.append("BOS retest valid")
        if raw.get("sponsorship_quality") == "HIGH":
            reasons.append("delta aligned")
        elif raw.get("sponsorship_quality") == "LOW":
            reasons.append("delta misaligned")
        if raw.get("mtf_alignment") == "FULL":
            reasons.append("MTF full")
        if not reasons:
            reasons = [f"grade={graded.get('grade','?')} conv={graded.get('score',0):.2f}"]

        return {
            "rts_family": rts_family,
            "offence_score": round(offence, 3),
            "defence_score": round(defence, 3),
            "trap_score": round(trap, 3),
            "intent": intent,
            "kill_level": kill_level,
            "auto_cut": auto_cut,
            "rts_reasons": reasons,
        }

    @staticmethod
    def _derive_action_state(intent: str, tier: str) -> str:
        """RTS-first action state. Intent drives the surface; legacy grade is diagnostic."""
        if intent in ("ATTACK_TRAP", "ATTACK_BREAK", "ATTACK"):
            return "CLICK"
        if intent == "PROBE":
            return "CLICK" if tier == "TIER_A" else "WAIT"
        if intent == "WAIT":
            return "WAIT"
        return "REJECT"   # CUT, IGNORE

    @staticmethod
    def _should_cut_now(enriched: Dict[str, Any]) -> bool:
        """Return True if the signal should be moved to killed rather than live."""
        if enriched.get("auto_cut") and enriched.get("intent") in ("CUT", "IGNORE"):
            return True
        # Zone second-touch auto-cut
        zone_touches = enriched.get("zone_touches")
        zone_touches = 0 if zone_touches is None else int(zone_touches)

        if enriched.get("rts_family") == "ZONE" and zone_touches >= 2:
            return True
        # CHOCH invalidated (flip_confirmed=False means partial/failed)
        if enriched.get("rts_family") == "CHOCH" and enriched.get("flip_confirmed") is False:
            return True
        return False

    @staticmethod
    def _fire_alerts(sammy: List[Dict[str, Any]], quiet: bool, sprint_mode: bool = False) -> None:
        """Emit Sammy Telegram alerts for S-grade CLEAN signals.

        S-grade signals fire even during quiet hours. Each alert includes
        pair, direction, conviction score, entry/SL/TP, and its TIER_A/TIER_B
        tag; sprint mode is called out explicitly when active.
        """
        for s in sammy:
            tier = s.get("tier", "TIER_B")
            mode_tag = " ⚡ SPRINT MODE" if sprint_mode else ""
            sizing = s.get("position_sizing", {})
            size_lines = []
            for seat, data in sizing.items():
                if data.get("mode") == "PROTECT_ONLY":
                    size_lines.append(f"{seat}: PROTECT ONLY")
                else:
                    size_lines.append(f"{seat}: {data['units']} units | ${data['dollar_risk']} risk")
            sizing_block = "\n".join(size_lines) if size_lines else "Sizing: N/A"
            message = (
                f"*{s['grade']}-GRADE SIGNAL*{mode_tag}\n"
                f"*{s['pair']}* {s['bias']} — {tier}\n"
                f"Engine: {s['engine']} | Conviction: {s['conviction']:.2f}\n"
                f"Entry: {s['entry']} | SL: {s['sl']} | TP: {s['tp']}\n"
                f"Regime: {s.get('regime', '?')} | R:R {s.get('rr', '?')}\n"
                f"{sizing_block}"
            )
            if quiet:
                logger.info("[SAMMY quiet-hours S-grade] %s %s %s conv=%.2f",
                            s["pair"], s["bias"], s["engine"], s["conviction"])
            else:
                logger.info("[SAMMY ALERT] %s %s %s conv=%.2f grade=%s tier=%s",
                            s["pair"], s["bias"], s["engine"], s["conviction"],
                            s["grade"], tier)
            # S-grade CLEAN signals are Sammy-eligible even in quiet hours.
            send_sammy(message)


def _is_quiet_hours() -> bool:
    """True during quiet hours (10PM-5AM CDT)."""
    hour = _now_cdt_hour()
    return hour >= 22 or hour < 5


if __name__ == "__main__":
    # Keep a manual run rate-limit friendly by capping analyzed pairs.
    scanner = TakScannerV3(max_pairs=None)  # all 54 prop pairs + any extras that pass filters
    results = scanner.run_scan()
    print(f"Scan complete: {results['signals_fired']} signals, "
          f"{results['signals_killed']} killed, "
          f"{results['s_grade_count']} S-grade "
          f"({results['scan_duration_sec']}s)")
