"""conviction_scorer.py — JHL V2 Unified Conviction Scorer.

8-factor weighted score in three buckets (core / defensive / offensive).
Conviction score 0-100 IS the grade — no separate letter grade system.
Feed threshold labels (S/A) are filter names only, not signal labels.

Bonus system: offensive + defensive premium stacking, capped at 3.0×.
Canonical signal output: score_8, action_state, action_reason per contract v1.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("conviction_scorer")

MODULE_DIR  = Path(__file__).resolve().parent
MODELS_DIR  = MODULE_DIR / "models"

# ── Hard gates ────────────────────────────────────────────────────────────────
MIN_RR        = 2.0
SCORE_CAP     = 99.0   # raw 0-100 scale
MAX_BONUS_MULT = 3.0   # bonuses stack up to 3×

# ── Feed threshold names (filter only — NOT stamped on signals) ───────────────
FEED_S_THRESHOLD = 88
FEED_A_THRESHOLD = 75

# ── MTF multipliers ───────────────────────────────────────────────────────────
MTF_MULTIPLIERS = {"FULL": 1.20, "PARTIAL": 1.00, "CONFLICT": 0.88}

# ── Three-bucket base weights (must sum to 1.0) ───────────────────────────────
# Core structure — 40%
CORE_WEIGHTS: Dict[str, float] = {
    "structure_break":  0.12,
    "htf_bias":         0.10,
    "momentum_align":   0.08,
    "ob_proximity":     0.10,
}
# Defensive — 30%  (trap_risk is a PENALTY, so its contribution is subtracted)
DEF_WEIGHTS: Dict[str, float] = {
    "liq_sweep_quality":  0.08,
    "stop_hunt_recovery": 0.07,
    "absorption_strength":0.07,
    "trap_risk_penalty":  0.08,   # stored as weight; applied as −score×weight
}
# Offensive — 30%
OFF_WEIGHTS: Dict[str, float] = {
    "displacement_quality":       0.08,
    "reclaim_acceptance":         0.07,
    "inefficiency_path":          0.07,
    "volatility_compression_rel": 0.04,
    "relative_leadership":        0.04,
}

ALL_WEIGHTS = {**CORE_WEIGHTS, **DEF_WEIGHTS, **OFF_WEIGHTS}

# ── Engine regime eligibility ─────────────────────────────────────────────────
ENGINE_REGIMES: Dict[str, set] = {
    "S1":        {"TREND_UP", "TREND_DOWN"},
    "S2":        {"TREND_UP", "TREND_DOWN"},
    "S3":        {"VOLATILE", "TREND_DOWN", "TREND_UP"},
    "S4":        {"RANGE"},
    "S5":        {"TREND_UP", "TREND_DOWN"},
    "S6":        {"RANGE", "FEAR", "TREND_DOWN"},
    "S7":        {"RANGE"},
    "S8":        {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "S9":        {"FEAR", "TREND_DOWN"},
    "S10":       {"RANGE", "VOLATILE", "FEAR", "TREND_DOWN"},
    "RTS_LIQ":   {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "RTS_BOS":   {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "RTS_CHOCH": {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "RTS_ZONE":  {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "RTS_DELTA": {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "RTS_BOTTLE":{"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
}

LEARNING_RATE = 0.01


# ══════════════════════════════════════════════════════════════════════════════
# Helper — safe float
# ══════════════════════════════════════════════════════════════════════════════
def _sf(val: Any, default: float = 0.5) -> float:
    if val is None:
        return default
    try:
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return default


# ══════════════════════════════════════════════════════════════════════════════
class ConvictionScorer:
    """JHL V2 conviction scorer — 8-factor, 3-bucket, bonus up to 3×."""

    def __init__(self, models_dir: Optional[Path] = None) -> None:
        self.models_dir = models_dir or MODELS_DIR
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ── Weight persistence ────────────────────────────────────────────────────
    def _weights_path(self, engine: str) -> Path:
        return self.models_dir / f"scorer_weights_{engine.replace('/', '_')}.json"

    def load_weights(self, engine: str) -> Dict[str, float]:
        path = self._weights_path(engine)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                return {k: float(data.get(k, ALL_WEIGHTS[k])) for k in ALL_WEIGHTS}
            except Exception as exc:
                logger.warning("Bad weight file %s: %s", engine, exc)
        return dict(ALL_WEIGHTS)

    def _save_weights(self, engine: str, w: Dict[str, float]) -> None:
        try:
            self._weights_path(engine).write_text(json.dumps(w, indent=2))
        except OSError as exc:
            logger.error("Save weights %s: %s", engine, exc)

    # ── R:R ───────────────────────────────────────────────────────────────────
    @staticmethod
    def _rr(sig: Dict[str, Any]) -> float:
        try:
            e, sl, tp = float(sig["entry"]), float(sig["sl"]), float(sig["tp"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        risk = abs(e - sl)
        return abs(tp - e) / risk if risk > 0 else 0.0

    # ── Core sub-scores (each 0-1) ────────────────────────────────────────────
    @staticmethod
    def _structure_break(sig: Dict[str, Any]) -> float:
        return _sf(sig.get("structure_quality"), 0.5)

    @staticmethod
    def _htf_bias(sig: Dict[str, Any]) -> float:
        bias = str(sig.get("bias", "")).upper()
        direction = str(sig.get("ai_st_direction", "")).upper()
        if not direction or direction == "NONE":
            return 0.5
        aligned = (bias == "LONG" and direction == "UP") or \
                  (bias == "SHORT" and direction == "DOWN")
        return 1.0 if aligned else 0.0

    @staticmethod
    def _momentum_align(sig: Dict[str, Any]) -> float:
        """Ribbon 25/50/100/200 alignment + AI ST strength as momentum proxy."""
        # Prefer explicit ribbon_score if engine computed it
        if "ribbon_score" in sig:
            return _sf(sig["ribbon_score"])
        # Fallback: RSI momentum proxy (engine-aware)
        try:
            rsi = float(sig.get("rsi", 50.0))
        except (TypeError, ValueError):
            rsi = 50.0
        bias = str(sig.get("bias", "")).upper()
        engine = str(sig.get("engine", "")).upper()
        trend = engine in {"S1", "S2", "S5"}
        if trend:
            return ((100 - rsi) / 100) if bias == "SHORT" else (rsi / 100)
        return ((100 - rsi) / 100) if bias == "LONG" else (rsi / 100)

    @staticmethod
    def _ob_proximity(sig: Dict[str, Any]) -> float:
        """Order-block / structure proximity — 1.0 = at OB, 0.5 = unknown."""
        return _sf(sig.get("ob_proximity", sig.get("order_block_proximity")), 0.5)

    # ── Defensive sub-scores ──────────────────────────────────────────────────
    @staticmethod
    def _liq_sweep_quality(sig: Dict[str, Any]) -> float:
        if "liq_sweep_quality" in sig:
            return _sf(sig["liq_sweep_quality"])
        swept = bool(sig.get("sweep_detected", False))
        depth = _sf(sig.get("sweep_depth"), 0.0)
        reclaim = _sf(sig.get("reclaim_close_ratio"), 0.0)
        if swept:
            return depth * 0.4 + reclaim * 0.6
        return 0.30  # no sweep = neutral, not zero

    @staticmethod
    def _stop_hunt_recovery(sig: Dict[str, Any]) -> float:
        if "stop_hunt_recovery" in sig:
            return _sf(sig["stop_hunt_recovery"])
        reclaim = _sf(sig.get("reclaim_close_ratio"), 0.0)
        accept  = min(int(sig.get("acceptance_bars") or 0), 5) / 5.0
        return reclaim * 0.5 + accept * 0.5

    @staticmethod
    def _absorption_strength(sig: Dict[str, Any]) -> float:
        if "absorption_strength" in sig:
            return _sf(sig["absorption_strength"])
        count = min(int(sig.get("absorption_count") or 0), 5) / 5.0
        vol   = _sf(sig.get("absorption_volume_ratio"), 0.3)
        # Fallback: simple volume ratio
        try:
            vr = float(sig.get("volume_ratio", 1.0))
        except (TypeError, ValueError):
            vr = 1.0
        vol_proxy = min(vr / 3.0, 1.0)
        return count * 0.4 + max(vol, vol_proxy) * 0.6

    @staticmethod
    def _trap_risk(sig: Dict[str, Any]) -> float:
        """Higher = more dangerous. Applied as penalty."""
        if "trap_risk" in sig:
            return _sf(sig["trap_risk"])
        bias = str(sig.get("bias", "LONG")).upper()
        eq_high = _sf(sig.get("equal_highs_distance"), 1.0)
        eq_low  = _sf(sig.get("equal_lows_distance"),  1.0)
        liq     = _sf(sig.get("liquidation_cluster_distance"), 1.0)
        prox = (1.0 - eq_high) if bias == "LONG" else (1.0 - eq_low)
        return prox * 0.6 + (1.0 - liq) * 0.4

    # ── Offensive sub-scores ──────────────────────────────────────────────────
    @staticmethod
    def _displacement_quality(sig: Dict[str, Any]) -> float:
        if "displacement_quality" in sig:
            return _sf(sig["displacement_quality"])
        body  = _sf(sig.get("impulse_body_ratio"), 0.5)
        follow= _sf(sig.get("follow_through_ratio"), 0.5)
        return body * 0.5 + follow * 0.5

    @staticmethod
    def _reclaim_acceptance(sig: Dict[str, Any]) -> float:
        if "reclaim_acceptance" in sig:
            return _sf(sig["reclaim_acceptance"])
        reclaim = _sf(sig.get("reclaim_close_ratio"), 0.5)
        accept  = min(int(sig.get("acceptance_bars") or 0), 5) / 5.0
        return reclaim * 0.6 + accept * 0.4

    @staticmethod
    def _inefficiency_path(sig: Dict[str, Any]) -> float:
        if "inefficiency_path" in sig:
            return _sf(sig["inefficiency_path"])
        # Proxy: clean R:R beyond 2.5 = cleaner air
        try:
            rr = float(sig.get("rr", 2.0))
        except (TypeError, ValueError):
            rr = 2.0
        return min((rr - 2.0) / 2.0, 1.0) * 0.5 + 0.5  # 0.5 floor

    @staticmethod
    def _volatility_compression(sig: Dict[str, Any]) -> float:
        if "volatility_compression_rel" in sig:
            return _sf(sig["volatility_compression_rel"])
        return _sf(sig.get("compression_ratio"), 0.5)

    @staticmethod
    def _relative_leadership(sig: Dict[str, Any]) -> float:
        if "relative_leadership" in sig:
            return _sf(sig["relative_leadership"])
        return _sf(sig.get("relative_atr_rank", sig.get("relative_volume_rank")), 0.5)

    # ── Composite scores ──────────────────────────────────────────────────────
    def _core_score(self, sig: Dict[str, Any], w: Dict[str, float]) -> float:
        raw = (
            w["structure_break"] * self._structure_break(sig) +
            w["htf_bias"]        * self._htf_bias(sig) +
            w["momentum_align"]  * self._momentum_align(sig) +
            w["ob_proximity"]    * self._ob_proximity(sig)
        )
        total_w = w["structure_break"] + w["htf_bias"] + w["momentum_align"] + w["ob_proximity"]
        return raw / total_w if total_w > 0 else 0.5

    def _defensive_score(self, sig: Dict[str, Any], w: Dict[str, float]) -> float:
        pos = (
            w["liq_sweep_quality"]   * self._liq_sweep_quality(sig) +
            w["stop_hunt_recovery"]  * self._stop_hunt_recovery(sig) +
            w["absorption_strength"] * self._absorption_strength(sig)
        )
        penalty = w["trap_risk_penalty"] * self._trap_risk(sig)
        raw = pos - penalty
        # Normalize to 0-1 band
        max_possible = w["liq_sweep_quality"] + w["stop_hunt_recovery"] + w["absorption_strength"]
        return max(0.0, min(raw / max_possible if max_possible > 0 else 0.5, 1.0))

    def _offensive_score(self, sig: Dict[str, Any], w: Dict[str, float]) -> float:
        total_w = sum(OFF_WEIGHTS.values())
        raw = (
            w["displacement_quality"]       * self._displacement_quality(sig) +
            w["reclaim_acceptance"]         * self._reclaim_acceptance(sig) +
            w["inefficiency_path"]          * self._inefficiency_path(sig) +
            w["volatility_compression_rel"] * self._volatility_compression(sig) +
            w["relative_leadership"]        * self._relative_leadership(sig)
        )
        return max(0.0, min(raw / total_w if total_w > 0 else 0.5, 1.0))

    # ── F&G alignment (context modifier, not a weighted bucket criterion) ─────
    @staticmethod
    def _fg_modifier(sig: Dict[str, Any]) -> float:
        """Returns a small ±0.03 multiplier nudge based on F&G alignment."""
        try:
            fg = float(sig.get("fg_score", 50))
        except (TypeError, ValueError):
            fg = 50.0
        bias   = str(sig.get("bias", "")).upper()
        engine = str(sig.get("engine", "")).upper()
        trend  = engine in {"S1", "S2", "S5"}
        if trend:
            if fg < 25:  return  0.03 if bias == "SHORT" else -0.02
            if fg > 75:  return  0.03 if bias == "LONG"  else -0.02
        else:
            if fg < 25:  return  0.03 if bias == "LONG"  else -0.02
            if fg > 75:  return  0.03 if bias == "SHORT" else -0.02
        return 0.0

    # ── Regime fit gate ───────────────────────────────────────────────────────
    @staticmethod
    def _regime_ok(sig: Dict[str, Any]) -> bool:
        engine = str(sig.get("engine", "")).upper()
        regime = str(sig.get("regime", "")).upper()
        allowed = ENGINE_REGIMES.get(engine)
        if allowed is None:
            return True   # unknown engine — pass through
        return regime in allowed

    # ── Bonus system (up to 3×) ───────────────────────────────────────────────
    def _compute_bonus(
        self,
        sig: Dict[str, Any],
        def_score: float,
        off_score: float,
    ) -> float:
        """Stack offensive + defensive premiums. Cap at MAX_BONUS_MULT."""
        bonus = 1.0   # start at 1× (no bonus)
        reasons: List[str] = []

        # Offensive premium triggers
        if self._displacement_quality(sig)  >= 0.85:
            bonus += 0.30; reasons.append("elite_displacement")
        if self._reclaim_acceptance(sig)    >= 0.82:
            bonus += 0.25; reasons.append("strong_reclaim")
        if self._inefficiency_path(sig)     >= 0.80:
            bonus += 0.20; reasons.append("clean_air_path")
        if self._volatility_compression(sig)>= 0.80:
            bonus += 0.15; reasons.append("compression_release")
        if self._relative_leadership(sig)   >= 0.82:
            bonus += 0.15; reasons.append("pair_leadership")
        if _sf(sig.get("liquidation_chain_potential"), 0.0) >= 0.75:
            bonus += 0.20; reasons.append("liquidation_chain")

        # Defensive premium triggers
        if self._stop_hunt_recovery(sig)    >= 0.85:
            bonus += 0.25; reasons.append("stop_hunt_recovered")
        if self._absorption_strength(sig)   >= 0.80:
            bonus += 0.20; reasons.append("absorption_confirmed")
        if self._liq_sweep_quality(sig)     >= 0.82:
            bonus += 0.20; reasons.append("clean_sweep_reclaim")

        # Combo bonuses
        if def_score >= 0.78 and off_score >= 0.78:
            bonus += 0.25; reasons.append("def+off_premium_combo")
        if self._reclaim_acceptance(sig) >= 0.82 and self._displacement_quality(sig) >= 0.85:
            bonus += 0.20; reasons.append("reclaim+displacement_combo")

        # MTF bonus
        mtf = str(sig.get("mtf_alignment", "PARTIAL")).upper()
        if mtf == "FULL":
            bonus += 0.10; reasons.append("full_mtf")

        final = min(bonus, MAX_BONUS_MULT)
        sig["_bonus_reasons"] = reasons
        return final

    # ── Action state ──────────────────────────────────────────────────────────
    @staticmethod
    def _action_state(
        conviction: float,
        def_score: float,
        off_score: float,
        trap: float,
        sig: Dict[str, Any],
    ) -> tuple[str, str]:
        """Returns (action_state, action_reason) per canonical contract."""

        # Hard reject conditions
        if trap >= 0.75:
            return "REJECT", f"Trap risk critical ({trap:.2f}) — likely raid terrain"
        if off_score < 0.35 and def_score < 0.40:
            return "REJECT", "Weak defensive and offensive — probable trap or fake setup"

        # Execute conditions
        if def_score >= 0.72 and off_score >= 0.68 and conviction >= FEED_A_THRESHOLD:
            sweep = bool(sig.get("sweep_detected", False))
            disp  = _sf(sig.get("displacement_quality"), 0.0)
            parts = []
            if sweep:     parts.append("sweep + reclaim confirmed")
            if disp >= 0.70: parts.append("strong displacement")
            if not parts: parts.append("structure + gateway alignment green")
            return "CLICK", "; ".join(parts)

        # Wait conditions
        parts = []
        if not sig.get("sweep_detected") and sig.get("rts"):
            parts.append("sweep not yet confirmed")
        if _sf(sig.get("reclaim_close_ratio"), 0.5) < 0.40:
            parts.append("reclaim not confirmed")
        if off_score < 0.68:
            parts.append(f"offensive score low ({off_score:.2f})")
        if conviction < FEED_A_THRESHOLD:
            parts.append(f"conviction below A threshold ({conviction:.0f})")
        return "WAIT", "; ".join(parts) if parts else "watching for confirmation"

    # ── Public: score() ───────────────────────────────────────────────────────
    def score(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Score a signal. Returns conviction 0-100, action_state, action_reason.

        No letter grades are stamped. Feed filters at >=88 (Sammy) and >=75 (A-tier).
        """
        engine = str(signal.get("engine", "S1")).upper()

        # Hard 2R gate
        rr = self._rr(signal)
        if rr < MIN_RR:
            logger.info("%s %s gated: R:R=%.2f < %.1f", signal.get("pair"), engine, rr, MIN_RR)
            return {
                "score_8": 0,
                "conviction": 0.0,
                "action_state": "REJECT",
                "action_reason": f"R:R {rr:.2f} below minimum {MIN_RR}",
                "defensive_score": 0.0,
                "offensive_score": 0.0,
                "trap_risk": 0.0,
                "bonus_multiplier": 1.0,
                "bonus_reasons": [],
                "breakdown": {"rr": round(rr, 3)},
            }

        # Regime gate — downgrade, not reject
        regime_ok = self._regime_ok(signal)

        w = self.load_weights(engine)

        core   = self._core_score(signal, w)
        def_s  = self._defensive_score(signal, w)
        off_s  = self._offensive_score(signal, w)
        trap   = self._trap_risk(signal)

        # Base score (0-1) — three buckets
        base = 0.40 * core + 0.30 * def_s + 0.30 * off_s

        # F&G context nudge
        base += self._fg_modifier(signal)
        base = max(0.0, min(base, 1.0))

        # MTF multiplier
        mtf  = str(signal.get("mtf_alignment", "PARTIAL")).upper()
        mult = MTF_MULTIPLIERS.get(mtf, 1.00)
        if not regime_ok:
            mult *= 0.80  # regime mismatch penalty, not kill

        after_mtf = min(base * mult, 1.0)

        # Bonus system — multiplied on top
        bonus = self._compute_bonus(signal, def_s, off_s)
        # Bonus amplifies distance above 0.5 baseline, doesn't create score from nothing
        boosted = 0.5 + (after_mtf - 0.5) * bonus if after_mtf > 0.5 else after_mtf
        final_01 = min(boosted, SCORE_CAP / 100.0)

        conviction_100 = round(final_01 * 100.0, 1)
        action, reason = self._action_state(conviction_100, def_s, off_s, trap, signal)

        logger.info(
            "%s %s -> conviction=%.1f action=%s [core=%.3f def=%.3f off=%.3f bonus=%.2f× mtf=%s]",
            signal.get("pair"), engine, conviction_100, action,
            core, def_s, off_s, bonus, mtf,
        )

        return {
            "score_8":          conviction_100,   # canonical field per snapshot spec
            "conviction":       conviction_100,
            "action_state":     action,
            "action_reason":    reason,
            "defensive_score":  round(def_s, 4),
            "offensive_score":  round(off_s, 4),
            "trap_risk":        round(trap, 4),
            "bonus_multiplier": round(bonus, 3),
            "bonus_reasons":    signal.pop("_bonus_reasons", []),
            "breakdown": {
                "rr":          round(rr, 3),
                "core":        round(core, 4),
                "defensive":   round(def_s, 4),
                "offensive":   round(off_s, 4),
                "base":        round(base, 4),
                "mtf":         mtf,
                "mtf_mult":    mult,
                "bonus":       round(bonus, 3),
                "regime_ok":   regime_ok,
            },
        }

    # ── Weight learning ───────────────────────────────────────────────────────
    def update_weights(self, signal: Dict[str, Any], outcome: bool) -> Dict[str, float]:
        engine = str(signal.get("engine", "S1")).upper()
        w = self.load_weights(engine)
        sub = {
            "structure_break":         self._structure_break(signal),
            "htf_bias":                self._htf_bias(signal),
            "momentum_align":          self._momentum_align(signal),
            "ob_proximity":            self._ob_proximity(signal),
            "liq_sweep_quality":       self._liq_sweep_quality(signal),
            "stop_hunt_recovery":      self._stop_hunt_recovery(signal),
            "absorption_strength":     self._absorption_strength(signal),
            "trap_risk_penalty":       self._trap_risk(signal),
            "displacement_quality":    self._displacement_quality(signal),
            "reclaim_acceptance":      self._reclaim_acceptance(signal),
            "inefficiency_path":       self._inefficiency_path(signal),
            "volatility_compression_rel": self._volatility_compression(signal),
            "relative_leadership":     self._relative_leadership(signal),
        }
        sign = 1.0 if outcome else -1.0
        for k in w:
            grad = sign * (sub.get(k, 0.5) - 0.5)
            w[k] = max(0.0, min(w[k] + LEARNING_RATE * grad, 1.0))
        total = sum(w.values()) or 1.0
        w = {k: v / total for k, v in w.items()}
        self._save_weights(engine, w)
        return w


# ── Module-level convenience ──────────────────────────────────────────────────
_scorer = None

def score_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Module-level shortcut used by the scanner pipeline."""
    global _scorer
    if _scorer is None:
        _scorer = ConvictionScorer()
    return _scorer.score(signal)


def is_sammy(conviction: float) -> bool:
    """True if conviction reaches the S-tier feed threshold (≥88)."""
    return conviction >= FEED_S_THRESHOLD


def is_a_tier(conviction: float) -> bool:
    """True if conviction reaches the A-tier feed threshold (≥75)."""
    return conviction >= FEED_A_THRESHOLD


if __name__ == "__main__":
    scorer = ConvictionScorer()
    demo = [
        {   # Elite setup — sweep + displacement + full MTF
            "pair": "BTC", "bias": "SHORT", "engine": "S1", "regime": "TREND_DOWN",
            "entry": 61000, "sl": 61800, "tp": 58600, "rsi": 68,
            "volume_ratio": 2.5, "ai_st_direction": "DOWN",
            "mtf_alignment": "FULL", "structure_quality": 0.85, "fg_score": 22,
            "sweep_detected": True, "sweep_depth": 0.9, "reclaim_close_ratio": 0.85,
            "acceptance_bars": 3, "displacement_quality": 0.88, "inefficiency_path": 0.82,
        },
        {   # Standard WAIT — decent structure, no sweep confirmation
            "pair": "SOL", "bias": "LONG", "engine": "S2", "regime": "TREND_UP",
            "entry": 81.0, "sl": 78.5, "tp": 87.0, "rsi": 40,
            "volume_ratio": 1.8, "ai_st_direction": "UP",
            "mtf_alignment": "PARTIAL", "structure_quality": 0.7, "fg_score": 55,
        },
        {   # Hard gate: R:R < 2
            "pair": "XRP", "bias": "LONG", "engine": "S1", "regime": "TREND_UP",
            "entry": 1.10, "sl": 1.05, "tp": 1.17, "rsi": 45,
            "volume_ratio": 1.2, "ai_st_direction": "UP",
            "mtf_alignment": "PARTIAL", "structure_quality": 0.6, "fg_score": 55,
        },
    ]
    for sig in demo:
        r = scorer.score(sig)
        print(f"{sig['pair']:5s} {sig['engine']} {sig['bias']:5s} → "
              f"conviction={r['conviction']:.1f} | {r['action_state']} | {r['action_reason'][:60]}")
        print(f"       def={r['defensive_score']:.3f} off={r['offensive_score']:.3f} "
              f"trap={r['trap_risk']:.3f} bonus={r['bonus_multiplier']:.2f}× "
              f"reasons={r['bonus_reasons']}")
