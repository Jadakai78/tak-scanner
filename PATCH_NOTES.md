# JHL v2 Patch Notes — Session 2026-07-09

## Summary
Wired S10 (Gimba Range Engine) into the TAK scanner, expanded engine regime
coverage, and rebuilt Remi with engine-aware kill logic. Scanner now produces
signals in TREND_DOWN markets where it previously returned zero.

---

## Files Modified

### strategies/s10_gimba_range.py (NEW FILE)
- BB(20,2) + RSI(14) mean-reversion engine on 1H candles
- Chop Index gate >= 61.8 (only fires in confirmed ranging conditions)
- ATR-based SL/TP with minimum 2R gate
- Fetches its own 1H OHLC independently from the 4H orchestrator feed

### strategies/__init__.py
- Added: `from .s10_gimba_range import S10GimbaRange`
- Added S10 to ENGINE_CLASSES
- Updated REGIME_ENGINES:
  - TREND_UP:   S1, S2, S5, S3, S6
  - TREND_DOWN: S1, S2, S5, S3, S6, S9, S10
  - VOLATILE:   S3, S6, S10
  - RANGE:      S4, S6, S7, S10
  - FEAR:       S6, S9, S10
  - DEAD:       []

### strategies/s3_gimba_volatile.py
- REQUIRED_REGIMES expanded: VOLATILE, TREND_DOWN, TREND_UP
  (was VOLATILE only — legacy long-only Kraken restriction)

### strategies/s6_reversal.py
- REQUIRED_REGIMES expanded: RANGE, FEAR, TREND_DOWN
- LEVEL_TOLERANCE loosened: 0.01 -> 0.025 (swing level proximity)
- OVEREXTENSION loosened: 0.015 -> 0.008 (EMA50 distance gate)
- ST flip requirement REMOVED from long_ok and short_ok conditions
- structure_quality floor set to max(0.55, wick_ratio) to prevent
  doji candles from scoring near zero

### strategies/s9_capitulation.py
- REQUIRED_REGIMES expanded: FEAR, TREND_DOWN
  (was FEAR only — legacy restriction)

### tak_scanner_v3.py
- _run_engine() signature updated: added pair_key and fetch_ohlc params
- generate() call updated: passes pair_key and fetch_ohlc to S10 only,
  legacy engines receive standard call (avoids unexpected kwarg errors)
- run_scan() call site updated: passes item pair_key and universe.fetch_ohlc
- MIN_VISIBLE_GRADE lowered: "B" -> "C"
- VISIBLE_GRADES expanded: {"C", "B", "A", "S"}

### conviction_scorer.py
- MTF_MULTIPLIERS CONFLICT penalty loosened: 0.70 -> 0.88
  (was too aggressive for counter-trend reversal signals)

### remi.py (REWRITTEN)
- Full engine-aware kill protocol replacing one-size-fits-all logic
- TREND_ENGINES = {S1, S2, S5} — HTF conflict is hard KILL
- COUNTER_TREND_ENGINES = {S3, S4, S6, S7, S9, S10} — HTF conflict is CAUTION only
- ENGINE_REQUIRED_REGIMES updated to match new expanded regime coverage
- F&G gates: trend engines use strict gates, counter-trend engines use
  extreme gates only (85/10 vs 75/15)
- _caution() method added for soft warnings vs hard kills
- All kills still logged to remi_kills.log for calibration

---

## Current Scan Status (as of session end)
- Scanner runs clean, no errors
- S6 firing on SOL, HYPE, ADA, NEAR in TREND_DOWN
- TIA S1 scoring C (0.510) — visible with new C threshold
- S6 base scores ~0.37-0.42 — need structure_quality improvement
- Next session: investigate S6 base score floor and ADA signal details

## Remaining Work
- [ ] Investigate why S6 base scores are still low (structure_quality feeding scorer)
- [ ] Check S3 GimbaVolatile — still returning None, review its detection logic
- [ ] Check S9 Capitulation — still returning None in TREND_DOWN
- [ ] S10 chop gate working correctly — market too trending to fire today
- [ ] Consider loosening S1 BOS retest tolerance from 0.5% to 1.0%
- [ ] Run full scan after market session to see if more signals fire
