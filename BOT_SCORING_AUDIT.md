# TAK-Scanner Bot Scoring Audit — Regime Awareness Analysis

**Date:** July 19, 2026  
**Focus:** Ensure bots fire in appropriate market conditions

---

## Executive Summary

You raised a critical question: **Why are traps and gimbas firing when they shouldn't be?**

The root issue: Some scoring logic may still rely on **legacy SMC structure detection** without full regime awareness. Since RTS are liquidation hunters, they (along with gimbas) should dominate in RANGE/VOLATILE markets, while trend-followers (S1, S2) should lead in TREND markets.

---

## Current Architecture (Per Gimba Handoff)

### Regime Classifier Outputs
- `TREND_UP` / `TREND_DOWN` → Directional bias confirmed
- `RANGE` → Choppy, mean-reverting conditions
- `VOLATILE` → High noise, whipsaw environment  
- `FEAR` / `DEAD` → Extreme risk-off or low liquidity

### Bot Assignments (SHOULD BE)

| Bot Type | Primary Regime | Should Fire When... |
|----------|----------------|---------------------|
| **S1 Sniper** | TREND | Strong directional bias + structure break |
| **S2 Trend Rider** | TREND | Established trend + pullback entry |
| **S3 Gimba Volatile** | VOLATILE | High volatility + KNN confidence |
| **S4 Mean Reversion** | RANGE | Overextension from mean |
| **S6 Reversal** | TREND (counter) | Exhaustion signals at extremes |
| **S7 Range Scalper** | RANGE | Inside consolidation zones |
| **S10 Gimba Range** | RANGE (REAL_CHOP) | Confirmed chop label + BB/RSI alignment |
| **RTS (all)** | RANGE/VOLATILE | Liquidation hunts work best in chop |
| **Trap Detector** | RANGE/VOLATILE | Fakeouts happen in noise, not trends |

---

## Known Issues

### 1. **S3 Gimba Volatile — Hardcoded TP Bug**
**Status:** Documented in JHL_GIMBA_HANDOFF_JULY18.md  
**Issue:** "Hardcoded × 2.0 TP" regardless of regime  
**Impact:** May take profit too early in volatile conditions  
**Fix Needed:** Dynamic TP based on ATR or volatility percentile

### 2. **MIN_RR Hardcoded in Conviction Scorer**
**Status:** ✅ FIXED (made configurable via env var)  
**Previous:** Hardcoded 2.0 R:R minimum  
**Now:** Configurable via `MIN_RR` environment variable

### 3. **Trap Detector Regime Awareness**
**Status:** ✅ GOOD — Has regime awareness via `april_system_view`  
**Logic:**
- Stand Down: 3+ trap kills, 4+ caution flags
- Time to Hunt: Clean flip opportunities exist
- Normal: Standard operation

**Concern:** Are traps being scored BEFORE regime filter?
- If a trap fires in a strong trend, it's likely a false positive
- Solution: Weight trap_score DOWN in TREND regimes

### 4. **RTS Engines in Trending Markets**
**Current Behavior:** RTS engines can fire anytime conviction ≥75  
**Problem:** Liquidation hunts are WEAK in strong trends  
**Example:**  
- Market: Strong TREND_UP  
- RTS_LIQ detects "liquidity grab" at support
- Fires long signal
- BUT: In trends, support often breaks (not a wick/trap)

**Proposed Fix:**
```python
# In RTS scoring logic
if regime in ["TREND_UP", "TREND_DOWN"]:
    conviction *= 0.6  # Penalize RTS in trends
    
if regime in ["RANGE", "VOLATILE"]:
    conviction *= 1.2  # Boost RTS in chop
```

### 5. **Gimba Bots — Chop Detection**
**S10 Gimba Range:**  
- Fires only when `chop_label = REAL_CHOP` ✅ GOOD
- Uses separate KNN brain ✅ GOOD
- Concern: Is REAL_CHOP aligned with regime_classifier RANGE output?

**S3 Gimba Volatile:**  
- Should fire in VOLATILE regime ✅ GOOD in theory
- Has KNN trained on volatility data ✅ GOOD
- Concern: Does it check regime BEFORE generating signals?

---

## Legacy SMC Structure Issues

Many bots may still score based on **classic SMC patterns** without regime context:

### Potentially Affected Bots:
- **S1 Sniper** — BOS/CHOCH detection
- **S2 Trend Rider** — FVG/OB entries  
- **S6 Reversal** — Liquidity sweeps at highs/lows
- **S7 Range Scalper** — Support/resistance bounces

### The Problem:
**Legacy SMC assumes:**
- BOS = always bullish (not true in ranging markets)
- Liquidity sweep = always reversal (not true in trends)
- FVG fill = always entry (not true in volatile whip)

**Modern Approach:**
- BOS in TREND regime = continuation ✅
- BOS in RANGE regime = likely fakeout ⚠️
- Liquidity sweep in TREND = stop hunt before continuation
- Liquidity sweep in RANGE = potential reversal

---

## Recommended Scoring Updates

### Priority 1: Add Regime Multipliers to All Specialists

Each specialist (S1-S10) should apply a regime-based conviction multiplier:

```python
def apply_regime_multiplier(base_conviction: float, regime: str, specialist_type: str) -> float:
    """
    Adjust conviction based on whether bot is firing in its optimal regime.
    """
    REGIME_MULTIPLIERS = {
        # Trend bots
        "S1_SNIPER": {"TREND_UP": 1.3, "TREND_DOWN": 1.3, "RANGE": 0.5, "VOLATILE": 0.4},
        "S2_TREND": {"TREND_UP": 1.4, "TREND_DOWN": 1.4, "RANGE": 0.6, "VOLATILE": 0.5},
        
        # Range/chop bots
        "S3_GIMBA_VOL": {"VOLATILE": 1.5, "FEAR": 1.2, "TREND_UP": 0.4, "TREND_DOWN": 0.4},
        "S4_MEAN_REV": {"RANGE": 1.3, "VOLATILE": 1.1, "TREND_UP": 0.7, "TREND_DOWN": 0.7},
        "S7_RANGE": {"RANGE": 1.4, "VOLATILE": 0.8, "TREND_UP": 0.5, "TREND_DOWN": 0.5},
        "S10_GIMBA_RANGE": {"RANGE": 1.5, "VOLATILE": 0.7, "TREND_UP": 0.3, "TREND_DOWN": 0.3},
        
        # RTS (liquidation hunters)
        "RTS_ALL": {"RANGE": 1.4, "VOLATILE": 1.3, "TREND_UP": 0.6, "TREND_DOWN": 0.6},
        
        # Reversal specialists
        "S6_REVERSAL": {"TREND_UP": 1.2, "TREND_DOWN": 1.2, "RANGE": 0.9, "VOLATILE": 0.7},
    }
    
    multiplier = REGIME_MULTIPLIERS.get(specialist_type, {}).get(regime, 1.0)
    return base_conviction * multiplier
```

### Priority 2: Update Trap Detector Scoring

Current logic: Scores traps based on structure alone  
**Add:**
```python
# In trap_detector.py evaluate function
if regime in ["TREND_UP", "TREND_DOWN"]:
    # Traps are RARE in strong trends — increase threshold
    TRAP_SCORE_HARD = 0.85  # Instead of 0.75
    TRAP_SCORE_CAUTION = 0.70  # Instead of 0.55
else:
    # Traps are COMMON in chop — use normal thresholds
    TRAP_SCORE_HARD = 0.75
    TRAP_SCORE_CAUTION = 0.55
```

### Priority 3: Fix S3 Gimba Volatile TP Logic

Replace:
```python
tp = entry * 2.0  # Hardcoded
```

With:
```python
atr = get_atr(pair, timeframe="15M")
volatility_percentile = get_volatility_percentile(pair)

if volatility_percentile > 0.8:  # Extremely volatile
    tp_mult = 3.0
elif volatility_percentile > 0.6:  # Moderately volatile  
    tp_mult = 2.5
else:
    tp_mult = 2.0
    
tp = entry + (atr * tp_mult * direction)
```

### Priority 4: S1/S2 Should Respect Regime

**S1 Sniper** (BOS/CHOCH specialist):  
- TREND regime: Fire normally ✅  
- RANGE regime: Require HIGHER conviction (e.g., 85 instead of 75)
- VOLATILE regime: Stand down (too much noise)

**S2 Trend Rider** (pullback specialist):  
- TREND regime: Prime conditions ✅
- RANGE regime: Pullbacks often fail — reduce conviction
- VOLATILE regime: Whipsaws kill pullback entries — stand down

---

## Action Items

### Immediate (This Week)
1. ✅ Make MIN_RR configurable (DONE)
2. **Audit all S-engine files** for regime awareness
3. **Add regime_classifier import** to all specialist files
4. **Test regime multipliers** on historical data

### Short-Term (Next 2 Weeks)
1. **Fix S3 Gimba Volatile TP** (remove hardcoded 2.0)
2. **Update trap_detector thresholds** based on regime
3. **Implement conviction multipliers** in conviction_scorer.py
4. **Add regime logging** to signal_bus.json for post-analysis

### Medium-Term (Next Month)
1. **Backtest regime-aware scoring** vs legacy scoring
2. **Tune multiplier coefficients** based on results
3. **Document regime decision tree** for each specialist
4. **Build regime transition detector** (TREND→RANGE alerts)

---

## Testing Protocol

### Step 1: Identify Current Regime
Run regime_classifier on recent market data:
```bash
python3 regime_classifier.py --pair BTC/USD --lookback 240
```

### Step 2: Compare Bot Fires to Regime
```python
# Pseudocode
for signal in signal_bus["signals"]:
    bot = signal["signal_name"]
    regime = signal["regime"]  # Add this field
    
    if bot in ["S1", "S2"] and regime in ["RANGE", "VOLATILE"]:
        print(f"⚠️  Trend bot {bot} fired in {regime} — investigate")
    
    if bot in ["S3", "S10", "RTS"] and regime in ["TREND_UP", "TREND_DOWN"]:
        print(f"⚠️  Range bot {bot} fired in {regime} — investigate")
```

### Step 3: Manual Signal Review
For 1 week, manually tag each signal:
- ✅ GOOD — Bot fired in correct regime
- ⚠️ QUESTIONABLE — Bot fired in suboptimal regime but worked
- ❌ BAD — Bot fired in wrong regime and failed

### Step 4: Calculate Regime Hit Rate
```
Good Regime Fires / Total Fires = Regime Alignment %

Target: >80% of signals fire in their optimal regime
```

---

## Conclusion

**You're absolutely right** — if traps and gimbas are firing when markets are trending, and RTS/gimbas aren't eating in chop, the scoring logic needs regime context.

**The fix:**  
1. Regime-aware conviction multipliers (boost bots in their zone, penalize outside)
2. Stricter thresholds for out-of-regime signals
3. Remove hardcoded values (like S3's TP and old MIN_RR)
4. Test and tune based on regime alignment metrics

**Bottom line:** RTS and gimbas should FEAST in ranging/choppy markets. Trend bots (S1/S2) should dominate in directional markets. Right now, the scoring doesn't enforce that.

Let's fix it. 🎯
