from pair_universe import PairUniverse
from strategies import ENGINE_CLASSES
import pandas as pd

u = PairUniverse()
pairs = u.get_active_pairs(interval=240)
item = next(i for i in pairs if i.get("pair") == "SOL")
df = pd.DataFrame(item["ohlc_4h"], columns=["time","open","high","low","close","vwap","volume","count"])
for col in ["open","high","low","close","vwap","volume"]:
    df[col] = pd.to_numeric(df[col], errors="coerce")
df = df.dropna().reset_index(drop=True)

for eid in ["S1","S2","S3","S5","S6","S9"]:
    try:
        result = ENGINE_CLASSES[eid]().generate("SOL", df, "TREND_DOWN", 22, {})
        print(eid + " -> " + str(result))
    except Exception as e:
        print(eid + " ERROR: " + str(e))
