"""JHL v2 strategy engines (S1-S10 + RTS family).

Each engine exposes REQUIRED_REGIMES and a generate(pair, ohlc_df, regime, fg_score, ai_st)
method that returns either None or a standardized signal dict.

S8 is an overlay / confirmer and is intentionally excluded from ENGINE_CLASSES.
"""

from strategies.s1_sniper import S1Sniper
from strategies.s2_trend_rider import S2TrendRider
from strategies.s3_gimba_volatile import S3GimbaVolatile
from strategies.s4_mean_reversion import S4MeanReversion
from strategies.s5_ema_cross import S5EMACross
from strategies.s6_reversal import S6Reversal
from strategies.s7_range_scalper import S7RangeScalper
from strategies.s8_mtf_confluence import S8MTFConfluence
from strategies.s9_capitulation import S9Capitulation
from strategies.s10_gimba_range import S10GimbaRange

from strategies.rts_liq import RTSLiq
from strategies.rts_choch import RTSChoch
from strategies.rts_bos import RTSBos
from strategies.rts_zone import RTSZone
from strategies.rts_delta import RTSDelta, score_delta_context
from strategies.rts_bottle import RTSBottle

ENGINE_CLASSES = {
    "S1": S1Sniper,
    "S2": S2TrendRider,
    "S3": S3GimbaVolatile,
    "S4": S4MeanReversion,
    "S5": S5EMACross,
    "S6": S6Reversal,
    "S7": S7RangeScalper,
    "S9": S9Capitulation,
    "S10": S10GimbaRange,

    # RTS family
    "RTS_LIQ": RTSLiq,
    "RTS_CHOCH": RTSChoch,
    "RTS_BOS": RTSBos,
    "RTS_ZONE": RTSZone,
    "RTS_DELTA": RTSDelta,
    "RTS_BOTTLE": RTSBottle,
}

REGIME_ENGINES = {
    "TREND_UP": [
        "S1", "S2", "S5",
        "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE",
    ],
    "TREND_DOWN": [
        "S1", "S2", "S5", "S10",
        "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE",
    ],
    "VOLATILE": [
        "S3", "S10",
        "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE",
    ],
    "RANGE": [
        "S4", "S6", "S7", "S10",
        "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE",
    ],
    "FEAR": [
        "S6", "S9", "S10",
        "RTS_LIQ", "RTS_CHOCH", "RTS_BOS", "RTS_ZONE", "RTS_BOTTLE",
    ],
    "DEAD": [],
}

__all__ = [
    "S1Sniper",
    "S2TrendRider",
    "S3GimbaVolatile",
    "S4MeanReversion",
    "S5EMACross",
    "S6Reversal",
    "S7RangeScalper",
    "S8MTFConfluence",
    "S9Capitulation",
    "S10GimbaRange",
    "RTSLiq",
    "RTSChoch",
    "RTSBos",
    "RTSZone",
    "RTSDelta",
    "RTSBottle",
    "score_delta_context",
    "REGIME_ENGINES",
    "ENGINE_CLASSES",
]
