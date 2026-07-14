# JHL Holdings — Trading Architecture v2 · Phase 1 (Foundation)

Four foundation modules that everything else in the architecture depends on.
Platform target: **Kraken US spot margin** (long *and* short via `type=sell` +
leverage). No futures engine. Pair universe is fully dynamic — no hardcoded
symbols anywhere.

## Modules

### `pair_universe.py` — `PairUniverse`
Dynamic Kraken USD pair discovery + ATR ranking (Layer 1, Data Ingestion).
- Pulls `GET /0/public/AssetPairs`, keeps USD/ZUSD-quoted pairs, drops
  stablecoins and dark-pool (`.d`) pairs.
- For each candidate fetches 4H OHLC (`interval=240`) and computes ATR%,
  24h USD volume (last 6 × 4H candles), RSI(14), and volume ratio.
- Filters: `volume_24h > $500K`, `ATR% >= 0.5%`. Ranks by ATR% desc.
- Writes the ranked list to `signal_bus.json` under `pair_universe`.
- Key method: `get_active_pairs(interval=240)`.

### `regime_classifier.py` — `RegimeClassifier`
AI regime detection — Random Forest with a rule-based bootstrap (AI Component 3).
- Outputs one of `TREND_UP / TREND_DOWN / RANGE / VOLATILE / FEAR / DEAD`.
- 10 features: `atr_pct_14/50`, `ema_slope_20/50`, `rsi_14`, `bb_width`,
  `volume_ratio`, `candle_overlap_ratio`, `fg_score`, `return_24h`.
- Uses deterministic rules until `tak_journal.csv` has ≥100 samples/class, then
  trains + persists `models/regime_rf.pkl` and prefers the RF.
- Key method: `classify(pair, ohlc_df, fg_score) -> str`.

### `ai_supertrend.py` — `AISupertrend`
KNN dynamic-multiplier SuperTrend (AI Component 1).
- Per-candle features: `[atr_pct, rsi, volume_ratio, body_ratio, wick_ratio]`.
- Finds the K=5 nearest historical candles, averages their "optimal multiplier"
  (the smallest multiplier that would have called the next-3-candle direction),
  clamps to `[1.0, 4.0]`. Cold start (<20 candles) uses default `2.5`.
- Persists per-pair history to `models/ai_st_{PAIR}.pkl`.
- Key method: `compute(pair, ohlc_df) -> {direction, multiplier, upper, lower,
  signal_strength}`.

### `conviction_scorer.py` — `ConvictionScorer`
Unified 0–1 conviction scorer + S8 MTF multiplier (AI Component 2).
- Weighted sum of 7 criteria (R:R, structure, AI-ST alignment, volume, regime
  fit, RSI quality, F&G alignment). S8 MTF multiplier: FULL ×1.15 (cap 0.99),
  PARTIAL ×1.00, CONFLICT ×0.70.
- Grades: `S ≥0.88 · A 0.75 · B 0.60 · C 0.45 · F <0.45`.
- **Hard 2R gate**: R:R < 2.0 → grade `F`, score `0.0`, `RR_BELOW_MINIMUM`.
- Per-engine weights load from `models/scorer_weights_{engine}.json`; online
  gradient step (`lr=0.01`) via `update_weights(signal, outcome)`.
- Key method: `score(signal_dict) -> {score, grade, breakdown}`.

## Shared state
`signal_bus.json` is the single source of truth the live feed reads. Each module
writes/merges its slice without clobbering the others.

## Running
Every module is independently runnable and demos against live BTC/SOL/XRP data:
```bash
python3 pair_universe.py
python3 regime_classifier.py
python3 ai_supertrend.py
python3 conviction_scorer.py
```

## Dependencies
`requests`, `numpy`, `pandas`, `scikit-learn`.
