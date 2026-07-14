from pair_universe import PairUniverse
from strategies._common import ema, supertrend, swing_highs, swing_lows
import pandas as pd

u = PairUniverse()
pairs = u.get_active_pairs(interval=240)
item = next(i for i in pairs if i.get("pair") == "SOL")
df = pd.DataFrame(item["ohlc_4h"], columns=["time","open","high","low","close","vwap","volume","count"])
for col in ["open","high","low","close","vwap","volume"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df = df.dropna().reset_index(drop=True)

last = float(df["close"].iloc[-1])
window = df.tail(50)
low_idxs = swing_lows(window)
high_idxs = swing_highs(window)
lows = [float(window["low"].iloc[i]) for i in low_idxs]
highs = [float(window["high"].iloc[i]) for i in high_idxs]

print("current price: " + str(round(last, 4)))
print("raw swing lows: " + str([round(l,4) for l in lows]))
print("raw swing highs: " + str([round(h,4) for h in highs]))
print("lows BELOW price: " + str([round(l,4) for l in lows if l < last]))
print("highs ABOVE price: " + str([round(h,4) for h in highs if h > last]))
near_low_1pct = any(abs(last - lv) / last <= 0.01 for lv in lows)
near_low_25pct = any(abs(last - lv) / last <= 0.025 for lv in lows)
print("near_low at 1pct: " + str(near_low_1pct))
print("near_low at 2.5pct: " + str(near_low_25pct))
