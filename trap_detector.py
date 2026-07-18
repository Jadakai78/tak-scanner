"""trap_detector.py — RTS Trap Detection & Kill Chain Layer.

Every scan cycle this module runs AFTER the S1-S9 specialists have produced
their signals. It takes the current live signal list plus fresh RTS outputs
for each pair and does four things:

  1. DETECT   — For every live S-engine signal, check if an RTS specialist
                 has fired a conflicting trap on the same pair + opposite or
                 same-side with ATTACK_TRAP / CUT intent.

  2. EVALUATE — Score the trap using trap_score, reclaim_status, kill_level
                 breach, and the 2-candle max doctrine from RTSRules Rule 2.

  3. KILL     — If the kill condition is met, mark the parent S-engine signal
                 as TRAP_KILLED and remove it from the live bucket.
                 Append it to killed_signals with kill_reason = TRAP_DETECTED.

  4. EMIT     — If the RTS signal qualifies for an ATTACK_TRAP re-entry
                 (the flip setup), surface it as a new live signal alongside
                 the kill notice.

Kill trigger hierarchy (first match wins):
  HARD_KILL   — kill_level physically breached on the current bar.
  TRAP_KILL   — trap_score >= TRAP_SCORE_HARD and reclaim_status = RECLAIMED.
  CAUTION     — trap_score >= TRAP_SCORE_CAUTION (2-candle watch, not a kill yet).
  CLEAR       — no trap — signal lives.

Architecture note (no router between pair / box / specialist):
  Each pair context carries its own RTS outputs. The detector reads them
  directly. There is no central routing layer between the pair data, the
  RTS box, and the S-engine specialist — the RTS box speaks first-class.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("trap_detector")

# ── Thresholds ────────────────────────────────────────────────────────────────

# trap_score at or above this → hard trap kill (no candle grace)
TRAP_SCORE_HARD: float = 0.75

# trap_score at or above this (but below HARD) → 2-candle caution window
TRAP_SCORE_CAUTION: float = 0.55

# If the RTS flip signal conviction is at or above this, surface it as live
RTS_FLIP_MIN_CONVICTION: float = 0.0   # RTS signals are conviction-agnostic;
                                        # intent gates them instead

# RTS intents that constitute an active trap
TRAP_INTENTS = {"ATTACK_TRAP", "CUT", "IGNORE"}

# RTS intents that mean a valid flip re-entry exists
FLIP_INTENTS = {"ATTACK_TRAP", "ATTACK_BREAK", "PROBE"}

# S-engine names (anything not in this set is an RTS engine)
S_ENGINES = {"S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10"}

# RTS engine names
RTS_ENGINES = {
    "RTS_LIQ", "RTS_BOS", "RTS_CHOCH", "RTS_ZONE", "RTS_DELTA", "RTS_BOTTLE"
}


# ── Data helpers ──────────────────────────────────────────────────────────────

def _sf(val: Any, default: float = 0.0) -> float:
    """Safe float cast."""
    try:
        f = float(val)
        import math
        return default if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Kill condition evaluator ──────────────────────────────────────────────────

def _evaluate_kill_condition(
    s_signal: Dict[str, Any],
    rts_signal: Dict[str, Any],
    current_bar: Optional[Dict[str, Any]],
) -> Tuple[str, str]:
    """
    Returns (verdict, reason).

    verdict: HARD_KILL | TRAP_KILL | CAUTION | CLEAR
    reason:  human-readable kill reason string
    """
    trap_score   = _sf(rts_signal.get("trap_score"), 0.0)
    kill_level   = rts_signal.get("kill_level")
    reclaim      = str(rts_signal.get("reclaim_status", "UNCLEAR")).upper()
    intent       = str(rts_signal.get("intent", "WAIT")).upper()
    rts_family   = str(rts_signal.get("rts_family", "")).upper()
    s_bias       = str(s_signal.get("bias", "LONG")).upper()
    rts_bias     = str(rts_signal.get("bias", "SHORT")).upper()

    # ── HARD_KILL: kill_level physically breached on current bar ─────────────
    if kill_level is not None and current_bar is not None:
        bar_high = _sf(current_bar.get("high"), 0.0)
        bar_low  = _sf(current_bar.get("low"),  0.0)
        # S is LONG → kill_level is below entry (stop out)
        # S is SHORT → kill_level is above entry (stop out)
        if s_bias == "LONG"  and bar_low  <= float(kill_level):
            return "HARD_KILL", f"kill_level {kill_level:.6f} breached (low={bar_low:.6f})"
        if s_bias == "SHORT" and bar_high >= float(kill_level):
            return "HARD_KILL", f"kill_level {kill_level:.6f} breached (high={bar_high:.6f})"

    # ── TRAP_KILL: strong trap confirmed by RTS, no physical breach needed ────
    if intent in TRAP_INTENTS:
        if trap_score >= TRAP_SCORE_HARD and reclaim == "RECLAIMED":
            return (
                "TRAP_KILL",
                f"RTS-{rts_family} trap confirmed: trap_score={trap_score:.2f} "
                f"reclaim=RECLAIMED intent={intent}",
            )
        if intent == "CUT":
            # CUT intent from any RTS engine is a hard kill — no grace period
            return (
                "TRAP_KILL",
                f"RTS-{rts_family} CUT intent: trap forming, skew gone intent={intent}",
            )

    # ── CAUTION: trap forming, 2-candle watch ─────────────────────────────────
    if trap_score >= TRAP_SCORE_CAUTION and intent in TRAP_INTENTS:
        return (
            "CAUTION",
            f"RTS-{rts_family} caution: trap_score={trap_score:.2f} intent={intent} "
            f"reclaim={reclaim} — 2-candle max watch",
        )

    return "CLEAR", ""


# ── Flip signal builder ───────────────────────────────────────────────────────

def _build_flip_event(
    rts_signal: Dict[str, Any],
    parent_signal_id: str,
    kill_reason: str,
) -> Dict[str, Any]:
    """
    Build a TRAP_FLIP event from an RTS signal.
    This is the re-entry opportunity that replaces the killed S-engine signal.
    Surfaces in the live bucket if intent qualifies.
    """
    intent = str(rts_signal.get("intent", "WAIT")).upper()
    qualifies = intent in FLIP_INTENTS

    return {
        "event_type":       "TRAP_FLIP",
        "ts":               _utc_now(),
        "pair":             rts_signal.get("pair"),
        "rts_family":       rts_signal.get("rts_family"),
        "bias":             rts_signal.get("bias"),
        "intent":           intent,
        "trap_score":       rts_signal.get("trap_score"),
        "offence_score":    rts_signal.get("offence_score"),
        "defence_score":    rts_signal.get("defence_score"),
        "kill_level":       rts_signal.get("kill_level"),
        "entry":            rts_signal.get("entry"),
        "sl":               rts_signal.get("sl"),
        "tp":               rts_signal.get("tp"),
        "pool_type":        rts_signal.get("liquidity_pool_type"),
        "reclaim_status":   rts_signal.get("reclaim_status"),
        "sweep_type":       rts_signal.get("sweep_type"),
        "kill_reason":      kill_reason,
        "parent_signal_id": parent_signal_id,
        "flip_qualifies":   qualifies,  # True = surface in live feed
    }


# ── Main detector ─────────────────────────────────────────────────────────────

class TrapDetector:
    """
    Runs after the main scan. Reads live S-engine signals and RTS outputs
    per pair, evaluates trap conditions, kills compromised signals, and
    emits TRAP_FLIP events for valid re-entries.

    Usage in tak_scanner_v4.py (after orchestrator.run):

        trap_detector = TrapDetector()
        live_signals, killed_by_trap, trap_flips = trap_detector.evaluate(
            live_signals=result.live_signals,   # List[PublishedSignal or dict]
            rts_outputs=rts_map,                # Dict[pair, List[rts_signal_dict]]
            current_bars=bar_map,               # Dict[pair, {high, low, close, open}]
        )
    """

    def evaluate(
        self,
        live_signals: List[Dict[str, Any]],
        rts_outputs: Dict[str, List[Dict[str, Any]]],
        current_bars: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parameters
        ----------
        live_signals : list of signal dicts (S1-S9 outputs that passed Remi+Council)
        rts_outputs  : dict keyed by pair, value = list of raw RTS signal dicts
        current_bars : dict keyed by pair, value = {high, low, close, open} of last bar

        Returns
        -------
        survivors    : signals that passed trap evaluation — go to bus as live
        trap_killed  : signals that were killed — go to bus as killed_signals
        trap_flips   : TRAP_FLIP events — valid RTS re-entries to surface in feed
        """
        current_bars = current_bars or {}
        survivors:   List[Dict[str, Any]] = []
        trap_killed: List[Dict[str, Any]] = []
        trap_flips:  List[Dict[str, Any]] = []

        for sig in live_signals:
            # Only evaluate S-engine signals — RTS signals are not subject to
            # trap killing by other RTS engines (they have their own CUT intent)
            engine = str(sig.get("engine", sig.get("specialist", ""))).upper()
            if engine not in S_ENGINES:
                survivors.append(sig)
                continue

            pair = str(sig.get("pair", ""))
            signal_id = str(sig.get("candidate_id", sig.get("signal_id", f"{pair}-{engine}")))
            pair_rts = rts_outputs.get(pair, [])
            current_bar = current_bars.get(pair)

            if not pair_rts:
                # No RTS output for this pair this cycle — signal lives
                survivors.append(sig)
                continue

            # Find the worst (most dangerous) RTS trap for this pair
            worst_verdict  = "CLEAR"
            worst_reason   = ""
            worst_rts      = None
            verdict_rank   = {"CLEAR": 0, "CAUTION": 1, "TRAP_KILL": 2, "HARD_KILL": 3}

            for rts_sig in pair_rts:
                rts_engine = str(rts_sig.get("engine", rts_sig.get("rts_family", ""))).upper()
                # Skip DELTA — it's an overlay confirmer, not a trap killer on its own
                if "DELTA" in rts_engine:
                    continue

                verdict, reason = _evaluate_kill_condition(sig, rts_sig, current_bar)

                if verdict_rank.get(verdict, 0) > verdict_rank.get(worst_verdict, 0):
                    worst_verdict = verdict
                    worst_reason  = reason
                    worst_rts     = rts_sig

            # ── Act on worst verdict ─────────────────────────────────────────
            if worst_verdict in ("HARD_KILL", "TRAP_KILL"):
                killed = dict(sig)
                killed["final_status"]  = "trap_killed"
                killed["kill_reason"]   = f"TRAP_DETECTED — {worst_reason}"
                killed["killed_at"]     = _utc_now()
                killed["killed_by"]     = worst_rts.get("engine", worst_rts.get("rts_family", "RTS"))
                trap_killed.append(killed)

                logger.warning(
                    "TRAP_KILLED %s %s %s | verdict=%s | reason=%s",
                    pair, engine, sig.get("bias"), worst_verdict, worst_reason,
                )

                # Build flip event from the RTS signal that fired the kill
                if worst_rts is not None:
                    flip = _build_flip_event(worst_rts, signal_id, worst_reason)
                    trap_flips.append(flip)
                    if flip["flip_qualifies"]:
                        logger.info(
                            "TRAP_FLIP qualified %s | intent=%s rts=%s",
                            pair, flip["intent"], flip["rts_family"],
                        )

            elif worst_verdict == "CAUTION":
                # Apply caution tag — signal survives but is flagged
                cautioned = dict(sig)
                cautioned["trap_caution"]        = True
                cautioned["trap_caution_reason"] = worst_reason
                cautioned["trap_caution_rts"]    = worst_rts.get("rts_family") if worst_rts else None
                cautioned["caution_issued_at"]   = _utc_now()
                survivors.append(cautioned)

                logger.info(
                    "TRAP_CAUTION %s %s %s | reason=%s",
                    pair, engine, sig.get("bias"), worst_reason,
                )

            else:
                # CLEAR — no trap, signal lives untouched
                survivors.append(sig)

        return survivors, trap_killed, trap_flips


# ── Remi trap integration ─────────────────────────────────────────────────────

def remi_trap_gate(
    signal: Dict[str, Any],
    rts_outputs: List[Dict[str, Any]],
    current_bar: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Remi-facing trap gate. Called from remi.py before the main evaluate()
    logic so Remi can apply the trap layer as a hard pre-check.

    Returns a dict:
        status  : KILLED | CAUTION | CLEAN
        reason  : kill/caution reason string or None
    """
    if not rts_outputs:
        return {"status": "CLEAN", "reason": None}

    verdict_rank = {"CLEAR": 0, "CAUTION": 1, "TRAP_KILL": 2, "HARD_KILL": 3}
    worst_verdict = "CLEAR"
    worst_reason  = ""

    for rts_sig in rts_outputs:
        rts_engine = str(rts_sig.get("engine", rts_sig.get("rts_family", ""))).upper()
        if "DELTA" in rts_engine:
            continue
        verdict, reason = _evaluate_kill_condition(signal, rts_sig, current_bar)
        if verdict_rank.get(verdict, 0) > verdict_rank.get(worst_verdict, 0):
            worst_verdict = verdict
            worst_reason  = reason

    if worst_verdict in ("HARD_KILL", "TRAP_KILL"):
        return {"status": "KILLED", "reason": f"TRAP — {worst_reason}"}
    if worst_verdict == "CAUTION":
        return {"status": "CAUTION", "reason": worst_reason}
    return {"status": "CLEAN", "reason": None}


# ── Council trap interface ────────────────────────────────────────────────────

def council_trap_summary(
    pair: str,
    trap_flips: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """
    Council-facing helper. Given a pair and the list of trap flip events
    from this scan cycle, returns the best-qualified flip re-entry for
    council claim arbitration, or None if no flip qualifies.

    Council reads this to decide whether to hunt the flip or stand down.
    """
    pair_flips = [f for f in trap_flips if f.get("pair") == pair and f.get("flip_qualifies")]
    if not pair_flips:
        return None

    # Pick highest offence_score flip
    best = max(pair_flips, key=lambda f: _sf(f.get("offence_score"), 0.0))
    return best


# ── April Field General interface ─────────────────────────────────────────────

def april_system_view(
    trap_killed:  List[Dict[str, Any]],
    trap_flips:   List[Dict[str, Any]],
    caution_count: int = 0,
) -> Dict[str, Any]:
    """
    April-facing summary of the trap situation across all pairs this cycle.
    April reads this to decide council mode: STAND_DOWN, TIME_TO_HUNT, NORMAL.

    Rules:
      - 3+ pairs trap-killed in one cycle  → STAND_DOWN
      - 1-2 pairs trap-killed with clean flips available → TIME_TO_HUNT
      - 0 trap kills, caution_count <= 2   → NORMAL
      - 0 trap kills, caution_count > 2    → STAND_DOWN (too much noise)
    """
    killed_pairs  = list({k["pair"] for k in trap_killed})
    flip_pairs    = list({f["pair"] for f in trap_flips if f.get("flip_qualifies")})
    killed_count  = len(killed_pairs)

    if killed_count >= 3 or caution_count > 4:
        mode = "STAND_DOWN"
        reason = (
            f"{killed_count} pairs trap-killed this cycle" if killed_count >= 3
            else f"{caution_count} caution signals — market too noisy"
        )
    elif killed_count >= 1 and flip_pairs:
        mode = "TIME_TO_HUNT"
        reason = f"{killed_count} trap(s) cleared, flip opportunities: {', '.join(flip_pairs)}"
    elif killed_count >= 1:
        mode = "STAND_DOWN"
        reason = f"{killed_count} trap(s) killed, no clean flip available yet"
    elif caution_count > 2:
        mode = "STAND_DOWN"
        reason = f"{caution_count} caution flags — too much trap noise to attack"
    else:
        mode = "NORMAL"
        reason = "No traps detected"

    return {
        "council_mode":   mode,
        "reason":         reason,
        "killed_pairs":   killed_pairs,
        "flip_pairs":     flip_pairs,
        "caution_count":  caution_count,
        "ts":             _utc_now(),
    }
