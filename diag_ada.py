from pair_universe import PairUniverse
from strategies import ENGINE_CLASSES
import pandas as pd

u = PairUniverse()
pairs = u.get_active_pairs(interval=240)

for sym in ["ADA", "NEAR", "SOL"]:
    item = next((i for i in pairs if i.get("pair") == sym), None)
    if not item:
        print(sym + ": not found")
        continue
    df = pd.DataFrame(item["ohlc_4h"], columns=["time","open","high","low","close","vwap","volume","count"])
    for col in ["open","high","low","close","vwap","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    result = ENGINE_CLASSES["S6"]().generate(sym, df, "TREND_DOWN", 22, {})
    if result:
        print(sym + " S6: bias=" + str(result.get("bias")) + " rr=" + str(result.get("rr")) + " sq=" + str(result.get("structure_quality")) + " entry=" + str(result.get("entry")))
    else:
        print(sym + " S6: None")
