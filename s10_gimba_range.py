"""
S10 - Gimba Range Engine
BB + RSI mean-reversion on 1H timeframe. Counter-trend engine.
Standard interface: REQUIRED_REGIMES + generate(). Self-fetches 1H OHLC.
"""
import krakenex
import pandas as pd
import numpy as np
from datetime import datetime

BB_PERIOD       = 20
BB_STD          = 2.0
RSI_PERIOD      = 14
RSI_OB          = 65
RSI_OS          = 35
MIN_BARS        = 50
KRAKEN_INTERVAL = 60  # 1H



def _normalize_pair(pair: str) -> str:
    """Ensure pair has USD suffix for Kraken REST API."""
    pair = pair.upper().strip()
    if pair.endswith("USD") or pair.endswith("USDT") or pair.endswith("XBT"):
        return pair
    # Handle BTC -> XBT (Kraken naming)
    if pair == "BTC":
        return "XBTUSD"
    return pair + "USD"
def _fetch_ohlc(pair: str, count: int = 200) -> pd.DataFrame:
    pair = _normalize_pair(pair)
    k = krakenex.API()
    resp = k.query_public("OHLC", {"pair": pair, "interval": KRAKEN_INTERVAL, "count": count})
    if resp.get("error"):
        raise RuntimeError(f"[S10] OHLC error {pair}: {resp['error']}")
    key = [x for x in resp["result"] if x != "last"][0]
    df = pd.DataFrame(resp["result"][key],
                      columns=["time","open","high","low","close","vwap","volume","count"])
    for col in ["open","high","low","close","vwap","volume"]:
        df[col] = df[col].astype(float)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df.set_index("time", inplace=True)
    return df


def _add_bb(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    rng = (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    df["bb_pct"]   = (df["close"] - df["bb_lower"]) / rng
    return df


def _add_rsi(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


class S10GimbaRange:
    REQUIRED_REGIMES = ["RANGE", "VOLATILE", "FEAR", "TREND_DOWN"]
    ENGINE_TYPE      = "COUNTER_TREND"

    def generate(self, pair: str, ohlc_df=None, regime: str = None,
                 fg_score: float = 50.0, ai_st=None, **kwargs):
        try:
            df = _fetch_ohlc(pair)
        except Exception as e:
            print(f"[S10] fetch failed {pair}: {e}")
            return None

        if len(df) < MIN_BARS:
            print(f"[S10] too few bars {pair}: {len(df)}")
            return None

        df = _add_bb(df)
        df = _add_rsi(df)

        last   = df.iloc[-1]
        rsi    = last["rsi"]
        bb_pct = last["bb_pct"]
        close  = last["close"]

        if pd.isna(rsi) or pd.isna(bb_pct):
            return None

        signal = direction = reason = None
        confidence = 0.0

        if bb_pct <= 0.15 and rsi <= RSI_OS:
            signal     = "LONG"
            direction  = "long"
            rsi_str    = max(0.0, (RSI_OS - rsi) / RSI_OS)
            bb_str     = max(0.0, (0.15 - bb_pct) / 0.15)
            confidence = round(min(0.90, 0.50 + rsi_str * 0.25 + bb_str * 0.25), 3)
            reason     = f"BB%={bb_pct:.2f} RSI={rsi:.1f} lower-band bounce LONG"

        elif bb_pct >= 0.85 and rsi >= RSI_OB:
            signal     = "SHORT"
            direction  = "short"
            rsi_str    = max(0.0, (rsi - RSI_OB) / (100.0 - RSI_OB))
            bb_str     = max(0.0, (bb_pct - 0.85) / 0.15)
            confidence = round(min(0.90, 0.50 + rsi_str * 0.25 + bb_str * 0.25), 3)
            reason     = f"BB%={bb_pct:.2f} RSI={rsi:.1f} upper-band fade SHORT"

        if signal is None:
            return None

        return {
            "engine_id":   "S10",
            "signal":      signal,
            "direction":   direction,
            "confidence":  confidence,
            "timeframe":   "1H",
            "reason":      reason,
            "engine_type": "COUNTER_TREND",
            "meta": {
                "rsi":      round(float(rsi), 2),
                "bb_pct":   round(float(bb_pct), 3),
                "bb_upper": round(float(last["bb_upper"]), 6),
                "bb_lower": round(float(last["bb_lower"]), 6),
                "bb_mid":   round(float(last["bb_mid"]), 6),
                "close":    round(float(close), 6),
            },
            "timestamp": datetime.utcnow().isoformat(),
        }


def run(pair: str, **kwargs):
    """Module-level shim for direct diagnostic calls."""
    return S10GimbaRange().generate(pair, **kwargs)

