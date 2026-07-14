from pair_universe import PairUniverse
from strategies._common import candle_anatomy, ema, supertrend, swing_highs, swing_lows
import pandas as pd

u = PairUniverse()
pairs = u.get_active_pairs(interval=240)
item = next(i for i in pairs if i.get("pair") == "SOL")
df = pd.DataFrame(item["ohlc_4h"], columns=["time","open","high","low","close","vwap","volume","count"])
for col in ["open","high","low","close","vwap","volume"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df = df.dropna().reset_index(drop=True)

row = df.iloc[-1]
close = df["close"]
last = float(close.iloc[-1])
anat = candle_anatomy(row)
e50 = float(ema(close, 50).iloc[-1])
st = supertrend(df, period=10, multiplier=3.0)
st_flip_up = bool(st.iloc[-1] == 1 and st.iloc[-2] == -1)
st_flip_down = bool(st.iloc[-1] == -1 and st.iloc[-2] == 1)
window = df.tail(50)
highs = [float(window["high"].iloc[i]) for i in swing_highs(window)]
lows = [float(window["low"].iloc[i]) for i in swing_lows(window)]
near_low = any(abs(last - lv) / last <= 0.01 for lv in lows)
near_high = any(abs(last - lv) / last <= 0.01 for lv in highs)

print("price=" + str(round(last,4)) + " e50=" + str(round(e50,4)))
print("st_flip_up=" + str(st_flip_up) + " st_flip_down=" + str(st_flip_down))
print("near_low=" + str(near_low) + " near_high=" + str(near_high))
print("lower_wick=" + str(round(anat["lower_wick"],4)) + " upper_wick=" + str(round(anat["upper_wick"],4)) + " body=" + str(round(anat["body"],4)) + " body_ratio=" + str(round(anat["body_ratio"],3)))
print("overext_low=" + str(last < e50 * 0.985) + " overext_high=" + str(last > e50 * 1.015))
print("swing lows: " + str([round(l,2) for l in sorted(lows)]))
print("swing highs: " + str([round(h,2) for h in sorted(highs)]))
