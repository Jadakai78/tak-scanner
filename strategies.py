"""
strategies.py
Compatibility shim / registry for tak_scanner_v3.py

Put this file in the same folder as tak_scanner_v3.py.
It exports:
- ENGINE_CLASSES
- REGIME_ENGINES
- S8MTFConfluence
- score_delta_context

You will likely need to adjust the import lines below so they match your
actual filenames in the repo.
"""

from __future__ import annotations

from typing import Any, Dict

# --- Import your engine classes here ---
# CHANGE THESE IMPORTS to match your real filenames.

# Examples based on the files you've shown:
from s8_mtf_confluence import S8MTFConfluence
from s6_reversal import S6Reversal
from s7_range_scalper import S7RangeScalper
from s9_capitulation import S9Capitulation

# If you have these RTS files, uncomment/fix them:
# from rts_bottle import RTSBottle
# from rts_liq import RTSLiquidity
# from rts_zone import RTSZone
# from rts_bos import RTSBOS


# --- Safe fallback for delta context ---
def score_delta_context(df, bias: str) -> Dict[str, Any]:
    """
    Minimal fallback so tak_scanner_v3.py can run even if the real
    RTS delta module is missing.
    """
    return {
        "delta_bias": "NEUTRAL",
        "delta_modifier": 0.0,
        "sponsorship_quality": "UNKNOWN",
        "vp_context": None,
        "vpoc": None,
    }


# --- Engine registry ---
ENGINE_CLASSES = {
    "S6": S6Reversal,
    "S7": S7RangeScalper,
    "S9": S9Capitulation,
    # "RTSBOTTLE": RTSBottle,
    # "RTSLIQ": RTSLiquidity,
    # "RTSZONE": RTSZone,
    # "RTSBOS": RTSBOS,
}


# --- Regime -> engines mapping ---
REGIME_ENGINES = {
    "RANGE": ["S6", "S7"],
    "FEAR": ["S6", "S9"],
    "TRENDDOWN": ["S6", "S9"],
    "TRENDUP": [],
    "VOLATILE": [],
    "DEAD": [],
}
