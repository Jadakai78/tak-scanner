"""
phasepath.py

PhasePath — dynamic structure envelope for TakScannerV3.

This module inspects the 4H OHLC DataFrame (df) and writes structure-related
fields back into the raw signal dict so downstream components (grading, V2,
Remi, RTS, finalizesignal) can reason about:

- overall structurequality (0.0–1.0 band)
- BOS (break of structure) level / direction / retest validity
- CHOCH (change of character) level / direction / flip confirmation
- zone top / bottom / touch count / mitigation state
- continuationstatus (simple descriptor of continuation vs breakdown)

The first implementation intentionally stays conservative and avoids
aggressive pattern claims; it favours safe defaults and basic swing logic.
"""

from __future__ import annotations

from typing import Any, Dict

import pandas as pd


def _safe_structurequality_from_swings(df: pd.DataFrame) -> float:
    """
    Derive a coarse structurequality score from swing behaviour.

    Returns a float in [0.0, 1.0], where:
    - ~0.5 = neutral / mixed structure
    - >0.65 = cleaner / directional structure
    - <0.35 = choppy / noisy structure

    This is deliberately simple; PhasePath can be upgraded later.
    """
    if df.empty:
        return 0.5

    closes = df["close"].astype(float)
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)

    # Basic volatility / range metrics
    price_range = float(highs.max() - lows.min()) or 1.0
    avg_bar_range = float((highs - lows).abs().mean() or 0.0)

    # If bars are tiny relative to the full swing range, structure tends to be cleaner.
    range_ratio = avg_bar_range / price_range

    # Map range_ratio into a 0–1 quality band.
    # More noise (high range_ratio) → lower quality.
    # Less noise (low range_ratio) → higher quality.
    quality = 1.0 - max(0.0, min(range_ratio * 3.0, 1.0))

    # Clamp to [0.0, 1.0]
    if quality < 0.0:
        quality = 0.0
    elif quality > 1.0:
        quality = 1.0

    # Nudge towards neutral if we have very few bars.
    if len(df) < 80:
        quality = (quality + 0.5) / 2.0

    return quality


def _default_phase_fields() -> Dict[str, Any]:
    """
    Provide safe default values for all PhasePath-managed fields.
    Downstream code already expects these keys on raw/v2/rts envelopes.
    """
    return {
        # Global structure
        "structurequality": 0.5,
        "continuationstatus": "UNKNOWN",  # CONTINUATION / BREAKDOWN / UNKNOWN

        # BOS (break of structure)
        "boslevel": None,
        "bosdirection": None,          # UP / DOWN / None
        "bosretestvalid": False,

        # CHOCH (change of character)
        "chochdirection": None,        # UP / DOWN / None
        "chochlevel": None,
        "flipconfirmed": False,

        # Zone context
        "zonetop": None,
        "zonebottom": None,
        "zonetouches": 0,
        "zonemitigated": False,
    }


def apply_phasepath(raw: Dict[str, Any], df: pd.DataFrame, engine_id: str) -> None:
    """
    Attach PhasePath fields to the raw signal dict.

    This is called once per raw signal in TakScannerV3.runscan(), after:
    - AI-Supertrend enrichment
    - MTF alignment scoring
    - ATR / volume tagging
    - microstructure.enrich(...)

    The function must be non-throwing: any internal error falls back to
    sane defaults so one signal never kills the whole scan.
    """
    if df is None or df.empty:
        # No data → keep defaults and exit.
        defaults = _default_phase_fields()
        for k, v in defaults.items():
            raw.setdefault(k, v)
        return

    # Start from defaults so all fields exist.
    phase = _default_phase_fields()

    try:
        # 1) Global structurequality
        sq = _safe_structurequality_from_swings(df)
        phase["structurequality"] = sq

        # 2) Very basic continuation vs breakdown heuristic.
        #    For now, we just use recent close vs mid-range as a coarse proxy.
        closes = df["close"].astype(float)
        recent_close = float(closes.iloc[-1])
        mid_range = float((closes.max() + closes.min()) / 2.0)

        if sq > 0.65:
            # Cleaner structure: check where price sits relative to the swing.
            if recent_close > mid_range:
                phase["continuationstatus"] = "CONTINUATION"
            else:
                phase["continuationstatus"] = "BREAKDOWN"
        elif sq < 0.35:
            phase["continuationstatus"] = "BREAKDOWN"
        else:
            phase["continuationstatus"] = "UNKNOWN"

        # 3) Provide BOS/CHOCH/zone scaffolding.
        #    We do NOT attempt full pattern detection yet; we just ensure the
        #    keys exist so RTS / resolverts can gradually take over.
        #    Future upgrades can populate:
        #    - boslevel / bosdirection / bosretestvalid
        #    - chochlevel / chochdirection / flipconfirmed
        #    - zonetop / zonebottom / zonetouches / zonemitigated

    except Exception:
        # If anything goes wrong, keep defaults; PhasePath must be fail-safe.
        pass

    # Write all phase fields back into raw without clobbering any existing
    # fields that engines / microstructure may already have set.
    for key, value in phase.items():
        if key not in raw or raw.get(key) is None:
            raw[key] = value
