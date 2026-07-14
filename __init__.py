"""JHL v2 strategy engines (S1-S9).

Each engine exposes a class with class attributes ``ENGINE`` and
``REQUIRED_REGIMES`` and a ``generate(pair, ohlc_df, regime, fg_score,
ai_st=None)`` method returning a partial signal dict or ``None``.

S8 is an overlay (not standalone): ``S8MTFConfluence.score_mtf(...)``.
"""

from .s1_sniper import S1Sniper
from .s2_trend_rider import S2TrendRider
from .s3_gimba_volatile import S3GimbaVolatile
from .s4_mean_reversion import S4MeanReversion
from .s5_ema_cross import S5EMACross
from .s6_reversal import S6Reversal
from .s7_range_scalper import S7RangeScalper
from .s8_mtf_confluence import S8MTFConfluence
from .s9_capitulation import S9Capitulation

# RTS family engines
from .rts_liq import RTSLiq
from .rts_choch import RTSChoch
from .rts_bos import RTSBos
from .rts_zone import RTSZone
from .rts_delta import RTSDelta, score_delta_context

# Regime -> eligible standalone engines (S8 is an overlay applied separately).
# RTS engines run across all regimes — structure and liquidity don't need trend.
REGIME_ENGINES = {
    "TREND_UP":   ["S1", "S2", "S5", "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE"],
    "TREND_DOWN": ["S1", "S2", "S5", "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE"],
    "VOLATILE":   ["S3",              "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE"],
    "RANGE":      ["S4", "S6", "S7", "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE"],
    "FEAR":       ["S6", "S9",        "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE"],
    "DEAD": [],
}

ENGINE_CLASSES = {
    "S1": S1Sniper,
    "S2": S2TrendRider,
    "S3": S3GimbaVolatile,
    "S4": S4MeanReversion,
    "S5": S5EMACross,
    "S6": S6Reversal,
    "S7": S7RangeScalper,
    "S9": S9Capitulation,
    # RTS family
    "RTS_LIQ":   RTSLiq,
    "RTS_CHOCH": RTSChoch,
    "RTS_BOS":   RTSBos,
    "RTS_ZONE":  RTSZone,
    "RTS_DELTA": RTSDelta,
}

__all__ = [
    "S1Sniper", "S2TrendRider", "S3GimbaVolatile", "S4MeanReversion",
    "S5EMACross", "S6Reversal", "S7RangeScalper", "S8MTFConfluence",
    "S9Capitulation", "REGIME_ENGINES", "ENGINE_CLASSES",
    "RTSLiq", "RTSChoch", "RTSBos", "RTSZone", "RTSDelta", "RTSBottle",
    "score_delta_context",
]
