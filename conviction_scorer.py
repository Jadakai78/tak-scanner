"""conviction_scorer.py — Unified 0-1 conviction scorer + S8 MTF multiplier.

AI Component 2 of JHL Trading Architecture v2. Every strategy engine emits a raw
signal; this module grades it S/A/B/C/F on a single 0.0-1.0 scale so the whole
system speaks one language.

Scoring is a weighted sum of seven criteria (R:R, structure quality, AI
SuperTrend alignment, volume, regime fit, RSI quality, F&G alignment). The S8
Multi-Timeframe verdict then multiplies the base score. A hard 2R gate rejects
anything below R:R 2.0 before scoring.

Per-engine weights are loaded from ``models/scorer_weights_{engine}.json`` when
present and nudged by a small gradient step each time a trade outcome is logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("conviction_scorer")

MODULE_DIR = Path(__file__).resolve().parent
MODELS_DIR = MODULE_DIR / "models"

# Default criterion weights (per S1 engine baseline). Must sum to ~1.0.
BASE_WEIGHTS: Dict[str, float] = {
    "rr_ratio": 0.25,
    "structure_quality": 0.20,
    "ai_st_alignment": 0.15,
    "volume_ratio": 0.10,
    "regime_fit": 0.15,
    "rsi_quality": 0.10,
    "fg_alignment": 0.05,
}

# Each engine's required/eligible regime(s) for the regime_fit criterion.
# Must match the expanded REGIME_ENGINES routing in strategies/__init__.py
ENGINE_REQUIRED_REGIME: Dict[str, set] = {
    "S1": {"TREND_UP", "TREND_DOWN"},
    "S2": {"TREND_UP", "TREND_DOWN"},
    "S3": {"VOLATILE", "TREND_DOWN", "TREND_UP"},   # expanded July 9
    "S4": {"RANGE"},
    "S5": {"TREND_UP", "TREND_DOWN"},
    "S6": {"RANGE", "FEAR", "TREND_DOWN"},           # expanded July 9
    "S7": {"RANGE"},
    "S8": {"TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"},
    "S9": {"FEAR", "TREND_DOWN"},                    # expanded July 9
    "S10": {"RANGE", "VOLATILE", "FEAR", "TREND_DOWN"},  # Gimba Range engine
}

RR_CAP = 4.0        # R:R normalization cap
MIN_RR = 2.0        # hard gate
VOLUME_CAP = 3.0    # volume_ratio normalization cap
LEARNING_RATE = 0.01

# MTF multipliers — CONFLICT loosened to 0.88 for counter-trend engines (July 9)
# Trend engines (S1/S2/S5) still receive hard CONFLICT penalty via Remi kill
MTF_MULTIPLIERS = {"FULL": 1.20, "PARTIAL": 1.00, "CONFLICT": 0.88}
SCORE_CAP = 0.99

GRADE_THRESHOLDS = [
    ("S", 0.88),
    ("A", 0.75),
    ("B", 0.60),
    ("C", 0.45),
]


class ConvictionScorer:
    """Grades raw strategy signals on a unified 0-1 conviction scale.

    Attributes:
        models_dir: Directory holding per-engine weight JSON files.
    """

    def __init__(self, models_dir: Optional[Path] = None) -> None:
        """Initialize the scorer.

        Args:
            models_dir: Override directory for per-engine weight files.
        """
        self.models_dir = models_dir or MODELS_DIR
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Weight persistence
    # ------------------------------------------------------------------
    def _weights_path(self, engine: str) -> Path:
        """Path to an engine's persisted weight file."""
        safe = str(engine).replace("/", "_")
        return self.models_dir / f"scorer_weights_{safe}.json"

    def load_weights(self, engine: str) -> Dict[str, float]:
        """Load per-engine weights, falling back to :data:`BASE_WEIGHTS`.

        Args:
            engine: Engine identifier (e.g. ``"S1"``).

        Returns:
            Weight dict keyed by criterion name.
        """
        path = self._weights_path(engine)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                # Only accept known criteria; fill any gaps from defaults.
                weights = {k: float(data.get(k, BASE_WEIGHTS[k])) for k in BASE_WEIGHTS}
                return weights
            except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
                logger.warning("Bad weight file for %s (%s) — using defaults.",
                               engine, exc)
        return dict(BASE_WEIGHTS)

    def _save_weights(self, engine: str, weights: Dict[str, float]) -> None:
        """Persist an engine's weight dict."""
        try:
            self._weights_path(engine).write_text(json.dumps(weights, indent=2))
        except OSError as exc:
            logger.error("Failed to save weights for %s: %s", engine, exc)

    # ------------------------------------------------------------------
    # Criterion sub-scores (each returns 0.0-1.0)
    # ------------------------------------------------------------------
    @staticmethod
    def _rr_value(signal: Dict[str, Any]) -> float:
        """Raw R:R ratio = reward/risk. Returns 0.0 on degenerate inputs."""
        try:
            entry = float(signal["entry"])
            sl = float(signal["sl"])
            tp = float(signal["tp"])
        except (KeyError, TypeError, ValueError):
            return 0.0
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0:
            return 0.0
        return reward / risk

    def _score_rr(self, signal: Dict[str, Any]) -> float:
        """Normalize R:R to [0,1], capped at R:R = :data:`RR_CAP`."""
        return min(self._rr_value(signal) / RR_CAP, 1.0)

    @staticmethod
    def _score_structure(signal: Dict[str, Any]) -> float:
        """Pass-through of the engine's structure_quality (clamped 0-1)."""
        return float(min(max(signal.get("structure_quality", 0.0), 0.0), 1.0))

    @staticmethod
    def _score_ai_st(signal: Dict[str, Any]) -> float:
        """1.0 if bias matches AI ST direction, 0.5 neutral, 0.0 conflict."""
        bias = str(signal.get("bias", "")).upper()
        direction = str(signal.get("ai_st_direction", "")).upper()
        want_up = bias == "LONG"
        st_up = direction == "UP"
        st_down = direction == "DOWN"
        if not (st_up or st_down):
            return 0.5  # neutral / unknown
        if (want_up and st_up) or ((not want_up) and st_down):
            return 1.0
        return 0.0

    @staticmethod
    def _score_volume(signal: Dict[str, Any]) -> float:
        """Volume quality: neutral at 1.0x avg, rewarded above, not punished below.

        Below 0.5x = 0.3 floor (not dead, just weak)
        0.5-1.0x   = scales 0.3-0.6 (normal range)
        1.0-3.0x   = scales 0.6-1.0 (above average = edge)
        Above 3.0x = capped at 1.0
        """
        try:
            vr = float(signal.get("volume_ratio", 1.0))
        except (TypeError, ValueError):
            vr = 1.0
        vr = max(vr, 0.0)
        if vr >= VOLUME_CAP:
            return 1.0
        if vr >= 1.0:
            return 0.6 + 0.4 * ((vr - 1.0) / (VOLUME_CAP - 1.0))
        if vr >= 0.5:
            return 0.3 + 0.3 * ((vr - 0.5) / 0.5)
        return 0.3  # floor — low volume, not zero

    @staticmethod
    def _score_regime(signal: Dict[str, Any]) -> float:
        """1.0 if the classified regime is in the engine's eligible set."""
        engine = str(signal.get("engine", "")).upper()
        regime = str(signal.get("regime", "")).upper()
        allowed = ENGINE_REQUIRED_REGIME.get(engine)
        if allowed is None:
            return 0.5  # unknown engine — neutral
        return 1.0 if regime in allowed else 0.0

    @staticmethod
    def _score_rsi(signal: Dict[str, Any]) -> float:
        """RSI quality — engine-aware.

        Trend engines (S1/S2/S5): momentum-friendly.
          LONG: RSI 40-65 = 0.8 (trend continuation zone), <30 = 1.0 (oversold snap),
                >70 = 0.4 (extended but not dead)
          SHORT: mirror image
        Reversal/range engines (S4/S6/S7/S9/S10): classic mean-reversion.
          LONG favors oversold, SHORT favors overbought.
        """
        try:
            rsi = float(signal.get("rsi", 50.0))
        except (TypeError, ValueError):
            rsi = 50.0
        rsi = min(max(rsi, 0.0), 100.0)
        bias = str(signal.get("bias", "")).upper()
        engine = str(signal.get("engine", "")).upper()
        trend_engines = {"S1", "S2", "S5"}
        if engine in trend_engines:
            # Trend: reward momentum zone, don't punish extended
            if bias == "LONG":
                if rsi < 30:   return 1.0   # oversold snap
                if rsi <= 65:  return 0.8   # healthy trend zone
                if rsi <= 75:  return 0.6   # extended but valid
                return 0.4                  # very extended
            else:  # SHORT
                if rsi > 70:   return 1.0
                if rsi >= 35:  return 0.8
                if rsi >= 25:  return 0.6
                return 0.4
        else:
            # Reversal/range: classic oversold/overbought scoring
            return (100 - rsi) / 100 if bias == "LONG" else rsi / 100

    @staticmethod
    def _score_fg(signal: Dict[str, Any]) -> float:
        """F&G alignment — engine-aware contrarian vs momentum.

        Trend engines (S1/S2/S5): momentum alignment.
          Greed + LONG = 1.0, Fear + SHORT = 1.0 (riding the wave)
          Extreme fear + LONG = 0.3 (fighting momentum)

        Reversal/counter-trend engines (S3/S4/S6/S7/S9/S10): contrarian.
          Extreme Fear (< 25) + LONG = 1.0 (Dragon buys fear)
          Extreme Greed (> 75) + SHORT = 1.0
          Neutral zone = 0.5
        """
        try:
            fg = float(signal.get("fg_score", 50))
        except (TypeError, ValueError):
            fg = 50.0
        bias = str(signal.get("bias", "")).upper()
        engine = str(signal.get("engine", "")).upper()
        trend_engines = {"S1", "S2", "S5"}

        if engine in trend_engines:
            # Momentum: ride the crowd
            if fg < 25:   return 1.0 if bias == "SHORT" else 0.3
            if fg < 45:   return 0.7 if bias == "SHORT" else 0.4
            if fg > 75:   return 1.0 if bias == "LONG"  else 0.3
            if fg > 55:   return 0.7 if bias == "LONG"  else 0.4
            return 0.5  # neutral zone
        else:
            # Contrarian: Dragon buys Extreme Fear
            if fg < 25:   return 1.0 if bias == "LONG"  else 0.2
            if fg < 40:   return 0.7 if bias == "LONG"  else 0.4
            if fg > 75:   return 1.0 if bias == "SHORT" else 0.2
            if fg > 60:   return 0.7 if bias == "SHORT" else 0.4
            return 0.5  # neutral zone

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @staticmethod
    def _grade(score: float) -> str:
        """Map a 0-1 score to a letter grade."""
        for grade, threshold in GRADE_THRESHOLDS:
            if score >= threshold:
                return grade
        return "F"

    def score(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Score a raw signal and return grade + breakdown.

        Args:
            signal: Raw signal dict (see module docstring / task spec for the
                expected keys).

        Returns:
            Dict with ``score`` (float), ``grade`` (str), and ``breakdown``
            (per-criterion contributions, weights, R:R, MTF multiplier). On the
            hard 2R gate it returns grade ``F`` / score ``0.0`` with a reason.
        """
        engine = str(signal.get("engine", "S1")).upper()

        # Hard 2R gate — reject before any scoring work.
        rr = self._rr_value(signal)
        if rr < MIN_RR:
            logger.info("%s %s gated: R:R=%.2f < %.1f",
                        signal.get("pair"), engine, rr, MIN_RR)
            return {
                "score": 0.0,
                "grade": "F",
                "reason": "RR_BELOW_MINIMUM",
                "breakdown": {"rr_ratio": round(rr, 3)},
            }

        weights = self.load_weights(engine)
        sub_scores = {
            "rr_ratio": self._score_rr(signal),
            "structure_quality": self._score_structure(signal),
            "ai_st_alignment": self._score_ai_st(signal),
            "volume_ratio": self._score_volume(signal),
            "regime_fit": self._score_regime(signal),
            "rsi_quality": self._score_rsi(signal),
            "fg_alignment": self._score_fg(signal),
        }

        base_score = sum(weights[k] * sub_scores[k] for k in BASE_WEIGHTS)

        # S8 MTF multiplier applied after the base score.
        mtf = str(signal.get("mtf_alignment", "PARTIAL")).upper()
        mult = MTF_MULTIPLIERS.get(mtf, 1.00)
        final_score = min(base_score * mult, SCORE_CAP)

        grade = self._grade(final_score)
        contributions = {k: round(weights[k] * sub_scores[k], 4) for k in BASE_WEIGHTS}

        result = {
            "score": round(final_score, 4),
            "grade": grade,
            "breakdown": {
                "rr": round(rr, 3),
                "base_score": round(base_score, 4),
                "mtf_alignment": mtf,
                "mtf_multiplier": mult,
                "sub_scores": {k: round(v, 4) for k, v in sub_scores.items()},
                "weights": {k: round(weights[k], 4) for k in BASE_WEIGHTS},
                "contributions": contributions,
            },
        }
        logger.info("%s %s -> %s (%.3f) [base=%.3f mtf=%s x%.2f]",
                    signal.get("pair"), engine, grade, final_score,
                    base_score, mtf, mult)
        return result

    def update_weights(self, signal: Dict[str, Any], outcome: bool) -> Dict[str, float]:
        """Nudge an engine's weights toward criteria that predicted the outcome.

        Simple online gradient step: for a winning trade (``outcome=True``) we
        push weights up on criteria that scored high and down on those that
        scored low; for a loss we do the reverse. Weights are clamped to
        [0, 1] then renormalized to sum to 1.0 and persisted.

        Args:
            signal: The original signal dict that was traded.
            outcome: True if the trade was a win, False if a loss.

        Returns:
            The updated (normalized) weight dict.
        """
        engine = str(signal.get("engine", "S1")).upper()
        weights = self.load_weights(engine)
        sub_scores = {
            "rr_ratio": self._score_rr(signal),
            "structure_quality": self._score_structure(signal),
            "ai_st_alignment": self._score_ai_st(signal),
            "volume_ratio": self._score_volume(signal),
            "regime_fit": self._score_regime(signal),
            "rsi_quality": self._score_rsi(signal),
            "fg_alignment": self._score_fg(signal),
        }
        # Error term: +1 for a win, -1 for a loss. Criteria centered at 0.5 so a
        # criterion that was "on" (>0.5) gets reinforced on wins.
        sign = 1.0 if outcome else -1.0
        for k in weights:
            grad = sign * (sub_scores[k] - 0.5)
            weights[k] = weights[k] + LEARNING_RATE * grad

        # Clamp and renormalize.
        for k in weights:
            weights[k] = max(0.0, min(weights[k], 1.0))
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}

        self._save_weights(engine, weights)
        logger.info("Updated %s weights (outcome=%s): %s",
                    engine, outcome, {k: round(v, 3) for k, v in weights.items()})
        return weights


if __name__ == "__main__":
    logger.info("=== ConvictionScorer demo ===")
    scorer = ConvictionScorer()

    demo_signals = [
        {  # Strong BTC short in fear-driven downtrend, full MTF alignment.
            "pair": "BTC", "bias": "SHORT", "engine": "S1", "regime": "TREND_DOWN",
            "entry": 61000, "sl": 61800, "tp": 58600, "rsi": 68,
            "volume_ratio": 2.5, "ai_st_direction": "DOWN", "ai_st_strength": 0.9,
            "mtf_alignment": "FULL", "structure_quality": 0.85, "fg_score": 22,
        },
        {  # SOL long, trend up, partial MTF.
            "pair": "SOL", "bias": "LONG", "engine": "S2", "regime": "TREND_UP",
            "entry": 81.0, "sl": 78.5, "tp": 87.0, "rsi": 40,
            "volume_ratio": 1.8, "ai_st_direction": "UP", "ai_st_strength": 0.8,
            "mtf_alignment": "PARTIAL", "structure_quality": 0.7, "fg_score": 55,
        },
        {  # XRP long but R:R below 2.0 -> hard gate.
            "pair": "XRP", "bias": "LONG", "engine": "S1", "regime": "TREND_UP",
            "entry": 1.10, "sl": 1.05, "tp": 1.17, "rsi": 45,
            "volume_ratio": 1.2, "ai_st_direction": "UP", "ai_st_strength": 0.6,
            "mtf_alignment": "PARTIAL", "structure_quality": 0.6, "fg_score": 55,
        },
        {  # XRP long, regime conflict (RANGE engine? no — S1 needs trend), CONFLICT MTF.
            "pair": "XRP", "bias": "LONG", "engine": "S1", "regime": "RANGE",
            "entry": 1.10, "sl": 1.06, "tp": 1.20, "rsi": 52,
            "volume_ratio": 0.9, "ai_st_direction": "DOWN", "ai_st_strength": 0.5,
            "mtf_alignment": "CONFLICT", "structure_quality": 0.4, "fg_score": 55,
        },
    ]

    for sig in demo_signals:
        res = scorer.score(sig)
        reason = f" reason={res['reason']}" if "reason" in res else ""
        print(f"{sig['pair']:5s} {sig['engine']} {sig['bias']:5s} "
              f"-> grade={res['grade']} score={res['score']:.3f}{reason}")

    # Demonstrate weight learning on a temp engine (won't clobber real weights).
    print("\nWeight learning demo (engine=DEMO):")
    demo_sig = dict(demo_signals[0])
    demo_sig["engine"] = "DEMO"
    before = scorer.load_weights("DEMO")
    scorer.update_weights(demo_sig, outcome=True)
    after = scorer.load_weights("DEMO")
    for k in BASE_WEIGHTS:
        print(f"  {k:20s} {before[k]:.4f} -> {after[k]:.4f}")
    # Clean up the demo weights file.
    (MODELS_DIR / "scorer_weights_DEMO.json").unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# V2 SHADOW SCORER — Phase 1 (non-destructive)
# Computes defensive_score, offensive_score, and conviction_v2 as shadow fields.
# Does NOT replace live conviction or grade. Runs after the main score().
# ══════════════════════════════════════════════════════════════════════════════

def _safe_v2(val, default: float = 0.5) -> float:
    """Normalize a raw field to 0-1, returning default if missing/None/NaN."""
    if val is None:
        return default
    try:
        import math
        f = float(val)
        return default if (math.isnan(f) or math.isinf(f)) else max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return default


def score_v2_shadow(signal: dict) -> dict:
    """Compute V2 defensive + offensive shadow scores.

    Uses the raw microstructure fields attached by microstructure.enrich().
    Returns a dict with:
        defensive_score     0-1
        offensive_score     0-1
        trap_risk           0-1  (higher = more dangerous)
        conviction_v2       0-1  (shadow — not live)
        v2_action           EXECUTE | WAIT | REJECT  (shadow)
        v2_reasons          list[str]

    All fields prefixed v2_ or named *_score to avoid collision with V1.
    """
    s = signal  # shorthand

    # ── Raw field extraction ─────────────────────────────────────────────────
    sweep_detected        = bool(s.get("sweep_detected", False))
    sweep_depth           = _safe_v2(s.get("sweep_depth"), 0.0)
    reclaim_close_ratio   = _safe_v2(s.get("reclaim_close_ratio"), 0.0)
    acceptance_bars       = min(int(s.get("acceptance_bars") or 0), 5) / 5.0
    absorption_count      = min(int(s.get("absorption_count") or 0), 5) / 5.0
    absorption_vol_ratio  = _safe_v2(s.get("absorption_volume_ratio"), 0.3)
    displacement_quality  = _safe_v2(s.get("displacement_quality"), 0.5)
    follow_through_ratio  = _safe_v2(s.get("follow_through_ratio"), 0.5)
    reclaim_acceptance    = _safe_v2(s.get("reclaim_close_ratio"), 0.0)  # reuse
    inefficiency_path     = _safe_v2(s.get("inefficiency_path"), 0.5)
    compression_ratio     = _safe_v2(s.get("compression_ratio"), 0.5)
    relative_leadership   = _safe_v2(s.get("relative_leadership"), 0.5)
    liq_cluster_dist      = _safe_v2(s.get("liquidation_cluster_distance"), 1.0)
    equal_highs_dist      = _safe_v2(s.get("equal_highs_distance"), 1.0)
    equal_lows_dist       = _safe_v2(s.get("equal_lows_distance"), 1.0)

    bias = str(s.get("bias", "LONG")).upper()

    # ── DEFENSIVE SCORE ──────────────────────────────────────────────────────
    # LIQUIDITY_SWEEP_QUALITY: reward sweep + strong reclaim
    if sweep_detected:
        sweep_quality = sweep_depth * 0.4 + reclaim_close_ratio * 0.6
    else:
        sweep_quality = 0.3  # no sweep detected = neutral (not penalized yet)

    # STOP_HUNT_RECOVERY: reclaim + acceptance bars
    stop_hunt_recovery = reclaim_close_ratio * 0.5 + acceptance_bars * 0.5

    # ABSORPTION_STRENGTH: absorption count + volume weight
    absorption_strength = absorption_count * 0.5 + absorption_vol_ratio * 0.5

    # TRAP_RISK: proximity to liquidation magnets (close = high trap risk)
    # Bias-aware: LONG entries near equal highs = bearish magnet overhead
    if bias == "LONG":
        trap_proximity = 1.0 - equal_highs_dist   # close to equal highs = trapped
    else:
        trap_proximity = 1.0 - equal_lows_dist    # close to equal lows = trapped
    trap_risk = trap_proximity * 0.6 + (1.0 - liq_cluster_dist) * 0.4

    # Defensive composite (TRAP_RISK inverted — high trap = low defensive)
    defensive_score = (
        sweep_quality       * 0.30 +
        stop_hunt_recovery  * 0.25 +
        absorption_strength * 0.20 +
        (1.0 - trap_risk)   * 0.25
    )

    # ── OFFENSIVE SCORE ──────────────────────────────────────────────────────
    # DISPLACEMENT_QUALITY
    # RECLAIM_ACCEPTANCE (proxy via reclaim_close_ratio + acceptance_bars)
    reclaim_accept = reclaim_close_ratio * 0.6 + acceptance_bars * 0.4

    # INEFFICIENCY_PATH: clean air to TP
    # VOLATILITY_COMPRESSION_RELEASE: compressed = ready to expand
    # RELATIVE_LEADERSHIP: this pair leading the universe

    offensive_score = (
        displacement_quality * 0.25 +
        reclaim_accept       * 0.20 +
        inefficiency_path    * 0.20 +
        compression_ratio    * 0.15 +
        relative_leadership  * 0.20
    )

    # ── V2 BASE SCORE ────────────────────────────────────────────────────────
    # Pull V1 core structure score (structure_quality + rr as proxy)
    structure_quality = _safe_v2(s.get("structure_quality"), 0.5)
    rr = float(s.get("rr") or 2.0)
    rr_norm = min(max((rr - 2.0) / 2.0, 0.0), 1.0)
    htf_bias = _safe_v2(
        s.get("score_components", {}).get("HTF_BIAS") if s.get("score_components") else None,
        0.5
    )
    momentum = _safe_v2(
        s.get("score_components", {}).get("MOMENTUM_ALIGN") if s.get("score_components") else None,
        0.5
    )
    core_structure = (structure_quality * 0.35 + htf_bias * 0.35 + momentum * 0.20 + rr_norm * 0.10)

    base_v2 = (core_structure * 0.40 + defensive_score * 0.30 + offensive_score * 0.30)

    # MTF multiplier (same as V1)
    mtf = str(s.get("mtf_verdict", "PARTIAL")).upper()
    from conviction_scorer import MTF_MULTIPLIERS
    mult = MTF_MULTIPLIERS.get(mtf, 1.00)
    conviction_v2 = min(base_v2 * mult, 0.99)

    # ── V2 ACTION ────────────────────────────────────────────────────────────
    reasons = []
    if defensive_score >= 0.72 and offensive_score >= 0.68 and trap_risk < 0.55:
        v2_action = "EXECUTE"
        if sweep_detected:
            reasons.append("sweep+reclaim confirmed")
        if displacement_quality >= 0.70:
            reasons.append("strong displacement")
        if inefficiency_path >= 0.75:
            reasons.append("clean path to TP")
    elif trap_risk >= 0.70:
        v2_action = "REJECT"
        reasons.append(f"trap risk high ({trap_risk:.2f})")
        if bias == "LONG" and equal_highs_dist < 0.3:
            reasons.append("equal highs overhead")
        elif bias == "SHORT" and equal_lows_dist < 0.3:
            reasons.append("equal lows below")
    elif offensive_score < 0.50 and defensive_score < 0.55:
        v2_action = "REJECT"
        reasons.append("weak defensive + offensive — likely trap")
    else:
        v2_action = "WAIT"
        if not sweep_detected:
            reasons.append("no sweep/reclaim yet")
        if reclaim_close_ratio < 0.40:
            reasons.append("reclaim not confirmed")
        if offensive_score < 0.68:
            reasons.append(f"offensive weak ({offensive_score:.2f})")

    return {
        "defensive_score": round(defensive_score, 4),
        "offensive_score": round(offensive_score, 4),
        "trap_risk": round(trap_risk, 4),
        "conviction_v2": round(conviction_v2, 4),
        "v2_action": v2_action,
        "v2_reasons": reasons,
        # Sub-components for display
        "v2_sweep_quality": round(sweep_quality, 3),
        "v2_stop_hunt_recovery": round(stop_hunt_recovery, 3),
        "v2_absorption": round(absorption_strength, 3),
        "v2_displacement": round(displacement_quality, 3),
        "v2_reclaim_accept": round(reclaim_accept, 3),
        "v2_path": round(inefficiency_path, 3),
        "v2_compression": round(compression_ratio, 3),
        "v2_leadership": round(relative_leadership, 3),
    }
