# TAK-SCANNER CONSOLIDATION ROADMAP
**Date:** July 19, 2026  
**Status:** Ready for execution  
**Goal:** Wire canonical snapshot contract, position Remi/April/Oracle correctly, eliminate stale data

---

## ARCHITECTURE POSITIONING

### **Correct Flow (5-Layer Model)**
```
1. ORACLE (pre-scan) → HTF bias (D1/W1 market_bias: LONG/SHORT/NEUTRAL)
2. BOX (scan) → S1-S9 engines generate setup candidates
3. REMI (post-signal gate) → Survival review (approved/caution/cut/kill based on trap_risk)
4. COUNCIL (claim arbitration) → RTS bots claim signals, lead_claimant assigned
5. APRIL (cross-pair supervisor) → Sets council mode (TIMETOHUNT/STANDDOWN based on multi-pair trap state)
```

### **Current Issues**
-  **Oracle** runs but output (`oracle_map`) isn't being used to filter/enhance signals pre-scan
-  **Remi** is treating signals as authoritative instead of front-end classifier
-  **April** doesn't exist as supervisor layer yet
-  **Stale positions** (AAVE/HYPE/SOL from yesterday) need time-based invalidation

---

## TASK BREAKDOWN

### **TASK 1: Fix S8 `pairkey` Signature Mismatch** ⚠️ HARD BLOCKER
**File:** `tak_scanner_v4.py` (or wherever S8MTFConfluence is called)

**Problem:**  
```python
TypeError: S8MTFConfluence.score_mtf() got an unexpected keyword argument 'pairkey'
```

**Fix:**  
Find where S8 is called (likely in orchestrator or specialist loop) and change:
```python
# WRONG
mtf_verdict = s8.score_mtf(pairkey=pair, df=df, ...)

# RIGHT
mtf_verdict = s8.score_mtf(pair_key=pair, df=df, ...)
```

**Search pattern:** `S8MTFConfluence`, `score_mtf`, `pairkey=`

---

### **TASK 2: Position Oracle Correctly (Pre-Scan HTF Bias)**
**File:** `tak_scanner_v4.py` → `run()` function

**Current:** Oracle runs and builds `oracle_map`, but it's only written to bus — not used to gate signals

**Fix:** After Oracle runs, inject `market_bias` into each pair's scan context:
```python
# After oracle runs
oracle_map = run_oracle(pair_list)

# When building contexts for each pair
for pair, df in pairs_data.items():
    htf_bias = oracle_map.get(pair, {}).get("market_bias", "NEUTRAL")
    context = {
        "pair": pair,
        "df": df,
        "oracle_htf_bias": htf_bias,  # ← NEW: inject HTF bias into context
        ...
    }
```

**Then in engines:** Check `context.get("oracle_htf_bias")` → suppress SHORT signals if HTF is LONG, etc.

---

### **TASK 3: Position Remi Correctly (Post-Signal Review Gate)**
**File:** `tak_scanner_v4.py` → After signal scoring, before publish

**Current:** Remi is being called somewhere in orchestrator, but outcome isn't exposed as first-class field

**Fix:** After scoring each signal, run Remi trap-risk check:
```python
def apply_remi_review(signal):
    trap_risk = signal.get("trap_risk", 0.0)
    reclaim_status = signal.get("reclaim_status", "none")
    
    if trap_risk >= 0.65 and reclaim_status == "RECLAIMED":
        return "kill"
    elif trap_risk >= 0.40:
        return "caution"
    else:
        return "approved"

# After signal scoring
for sig in candidates:
    sig["remi_review"] = apply_remi_review(sig)
    
    # KILL signals don't go to bus
    if sig["remi_review"] == "kill":
        killed_signals.append(sig)
        continue
```

**Key:** `remi_review` becomes a **first-class outward field** on every signal

---

### **TASK 4: Add April Layer (Cross-Pair Supervisor)**
**File:** New file `april_supervisor.py` or add to `tak_scanner_v4.py`

**Purpose:** April watches for multi-pair trap events and sets council mode

**Implementation:**
```python
def april_system_view(signals, killed_signals):
    """
    Cross-pair supervisor: if 3+ pairs trapped simultaneously, set mode to STANDDOWN.
    Otherwise, if single trap with clean RTS flip forming, set TIMETOHUNT.
    """
    trap_count = sum(1 for sig in killed_signals if sig.get("remi_review") == "kill")
    
    if trap_count >= 3:
        return {"mode": "STANDDOWN", "reason": f"{trap_count} simultaneous traps detected"}
    elif trap_count == 1:
        # Check if RTS flip signal exists for same pair
        killed_pair = killed_signals[0]["pair"]
        rts_flip = any(s["pair"] == killed_pair and "RTS" in s.get("engine", "") for s in signals)
        if rts_flip:
            return {"mode": "TIMETOHUNT", "reason": "Single trap with RTS flip forming"}
    
    return {"mode": "NORMAL", "reason": "No multi-pair trap state"}

# At end of scan
april_view = april_system_view(live_signals, killed_signals)
# Include in canonical snapshot under "alerts" or new "april" section
```

---

### **TASK 5: Wire Canonical Snapshot Builder**
**File:** `tak_scanner_v4.py` → end of `run()` function

**Current:** Returns raw dict with legacy keys (`last_scan`, `next_scan`, `live_signals`, etc.)

**Fix:** Import and call `signal_bus_schema.build_canonical_snapshot()`:
```python
from signal_bus_schema import build_canonical_snapshot

# At end of run()
snapshot = build_canonical_snapshot(
    last_scan=datetime.now(timezone.utc).isoformat(),
    next_scan=(datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
    fg_score=fear_greed_score,
    fg_label=fear_greed_label,
    active_pairs=len(active_pairs),
    dead_pairs=len(dead_pairs),
    universe_count=len(pair_universe),
    signals_fired=len(live_signals),
    signals_killed=len(killed_signals),
    s_grade_count=sum(1 for s in live_signals if s.get("grade") == "S"),
    scan_duration_sec=scan_duration,
    quiet_hours=quiet_hours_flag,
    worker_push_success=True,  # Set based on actual worker push result
    last_push_status="OK 200",
    scan_runtime_summary=f"Scan completed in {scan_duration:.2f}s with {len(live_signals)} signals",
    regime_map=regime_map,
    regime_counts={
        "TRENDUP": sum(1 for r in regime_map.values() if r == "TRENDUP"),
        "TRENDDOWN": sum(1 for r in regime_map.values() if r == "TRENDDOWN"),
        "RANGE": sum(1 for r in regime_map.values() if r == "RANGE"),
        "VOLATILE": sum(1 for r in regime_map.values() if r == "VOLATILE"),
        "DEAD": sum(1 for r in regime_map.values() if r == "DEAD"),
    },
    signals=live_signals,  # These should already have context objects from TASK 6
    alerts=[april_view["reason"], f"{fg_label} with {len(live_signals)} signals"],
    diagnostics=audit_log,  # MTF errors, S8 kwargs, HTTP timeouts
)

return snapshot  # ← This is what worker receives
```

---

### **TASK 6: Populate Context Objects in Engine Outputs**
**Files:** `S1_sniper.py`, `S2_trendrider.py`, etc. (all engine files)

**Current:** Engines return signals with basic fields (`pair`, `bias`, `entry`, `sl`, `tp`)

**Fix:** After engine generates signal, populate context objects:
```python
from _common import (
    ema_ribbon, build_trend_context, build_st_context, 
    build_volume_context, build_volatility_context, build_structure_context
)

def generate(pair, df, context):
    # ... existing engine logic to build signal ...
    
    # NEW: Populate context objects
    ribbon = ema_ribbon(df)
    signal["trend_context"] = build_trend_context(df, ribbon)
    signal["st_context"] = build_st_context(df)
    signal["volume_context"] = build_volume_context(df)
    signal["volatility_context"] = build_volatility_context(df)
    signal["structure_context"] = build_structure_context(df, signal["bias"])
    
    return signal
```

**Do this for ALL engines (S1-S10).**

---

### **TASK 7: Add Governance Fields**
**File:** `tak_scanner_v4.py` → After Remi review, before publish

**Current:** Signals don't have `council_claim`, `tak_publish_auth`, etc.

**Fix:** After Remi review and RTS promotion:
```python
for sig in live_signals:
    # Remi review (already added in TASK 3)
    sig["remi_review"] = apply_remi_review(sig)
    
    # Council claim (check if RTS bots have claimed this setup)
    rts_engines = ["RTS_LIQ", "RTS_BOS", "RTS_CHOCH", "RTS_ZONE", "RTS_BOTTLE"]
    if sig.get("engine") in rts_engines and sig.get("score", 0) >= 75:
        sig["council_claim"] = "lead_claim"
        sig["lead_claimant"] = sig["engine"]
    else:
        sig["council_claim"] = "no_claim"
        sig["lead_claimant"] = None
    
    # Tak publish auth (signals that passed Remi + met score threshold)
    sig["tak_publish_auth"] = sig["remi_review"] in ["approved", "caution"] and sig.get("score", 0) >= 62
    
    # Route reason
    if sig["tak_publish_auth"]:
        sig["route_reason"] = f"Published: {sig['remi_review']} by Remi, claimed by {sig.get('lead_claimant', 'none')}"
    else:
        sig["route_reason"] = "Rejected: failed Tak publish gate"
    
    # Veto reasons (empty for now, can be populated if Remi kills or Council rejects)
    sig["veto_reasons"] = []
```

---

### **TASK 8: Wire Dynamic SL/TP**
**Files:** All engine files (S1-S10)

**Current:** Engines probably use static ATR multiples like `sl = entry - atr * 1.5`

**Fix:** Replace with dynamic SL/TP calls:
```python
from _common import compute_dynamic_sl, compute_dynamic_tp, compute_rr

# After determining entry
sl = compute_dynamic_sl(df, bias, entry, atr_mult=1.5)
tp = compute_dynamic_tp(entry, sl, bias, target_rr=2.5, structure_target=nearest_swing_high)
rr = compute_rr(entry, sl, tp)

signal = {
    "entry": entry,
    "sl": sl,
    "tp": tp,
    "rr": rr,
    ...
}
```

**Do this for ALL engines.**

---

### **TASK 9: Add MACD/OBV Usage**
**Files:** `S2_trendrider.py` (momentum), `S5_emacross.py` (crossover), engines that need volume confirmation

**Fix:** Add MACD histogram confirmation for momentum signals:
```python
from _common import macd, obv

# In S2TrendRider or S5EMACross
macd_line, signal_line, histogram = macd(df["close"])
obv_series = obv(df)
obv_slope = (obv_series.iloc[-1] - obv_series.iloc[-5]) / abs(obv_series.iloc[-5]) if len(obv_series) >= 5 else 0.0

# Require MACD histogram > 0 for LONG, < 0 for SHORT
if bias == "LONG" and histogram.iloc[-1] <= 0:
    return None  # Reject signal
if bias == "SHORT" and histogram.iloc[-1] >= 0:
    return None

# Require OBV slope confirmation
if bias == "LONG" and obv_slope < 0:
    return None
if bias == "SHORT" and obv_slope > 0:
    return None
```

---

### **TASK 10: Add `/snapshot` Route to Worker**
**File:** `server.py` or worker file

**Current:** Worker likely has `/events` or root route, but no `/snapshot`

**Fix:**
```python
latest_snapshot = {}  # Global cache

@app.route("/snapshot", methods=["GET"])
def get_snapshot():
    """Serve the latest canonical snapshot from scanner."""
    if not latest_snapshot:
        return jsonify({"error": "No snapshot available"}), 503
    return jsonify(latest_snapshot)

# When scanner pushes to worker (existing push handler)
@app.route("/push", methods=["POST"])
def receive_push():
    global latest_snapshot
    latest_snapshot = request.json  # Cache the canonical snapshot
    logger.info("Snapshot updated")
    return jsonify({"status": "OK"}), 200
```

**Then set Railway `FEED_URL` env var to:** `https://your-worker-host/snapshot`

---

### **TASK 11: Fix Stale Positions**
**File:** `scheduler.py` or wherever position persistence is handled

**Current:** Positions (AAVE/HYPE/SOL) from yesterday are still showing

**Fix:** Add time-based invalidation:
```python
from datetime import datetime, timezone, timedelta

def filter_stale_positions(positions, max_age_hours=24):
    """Remove positions older than max_age_hours."""
    now = datetime.now(timezone.utc)
    fresh = []
    for pos in positions:
        created_at = datetime.fromisoformat(pos.get("created_at", ""))
        if (now - created_at).total_seconds() / 3600 < max_age_hours:
            fresh.append(pos)
        else:
            logger.info(f"Removed stale position: {pos['pair']} (age: {(now - created_at).total_seconds() / 3600:.1f}h)")
    return fresh

# When loading positions
positions = load_positions()
positions = filter_stale_positions(positions, max_age_hours=24)
```

---

## EXECUTION ORDER

| Step | Task | Why First |
|---|---|---|
| 1 | Fix S8 `pairkey` | Scans are dying mid-run |
| 2 | Wire canonical snapshot builder | Establishes ONE contract |
| 3 | Add `/snapshot` route to worker | UI needs endpoint |
| 4 | Populate context objects | Signals need structured context |
| 5 | Position Oracle (pre-scan HTF) | HTF bias filters signals early |
| 6 | Position Remi (post-signal gate) | Survival review before publish |
| 7 | Add governance fields | Track provenance |
| 8 | Add April supervisor | Cross-pair mode setting |
| 9 | Wire dynamic SL/TP | Better risk management |
| 10 | Add MACD/OBV | Momentum/volume confirmation |
| 11 | Fix stale positions | Clean up yesterday's data |

---

## VALIDATION CHECKLIST

After all tasks complete, verify:

- ✅ S8 scans complete without `pairkey` error
- ✅ Worker serves `/snapshot` with canonical schema
- ✅ All signals have `trend_context`, `st_context`, `volume_context`, `volatility_context`, `structure_context`
- ✅ All signals have `remi_review`, `council_claim`, `tak_publish_auth`, `route_reason`
- ✅ Oracle `market_bias` is injected into scan contexts
- ✅ Remi kills high trap-risk signals before they reach bus
- ✅ April view shows cross-pair mode (NORMAL/TIMETOHUNT/STANDDOWN)
- ✅ Positions older than 24h are pruned
- ✅ UI reads from canonical `/snapshot` endpoint

---

## FILE MAP

| Component | File |
|---|---|
| Canonical schema | `signal_bus_schema.py` ✅ (already created) |
| Common indicators | `_common.py` ✅ (already upgraded) |
| Scanner orchestrator | `tak_scanner_v4.py` (needs upgrades) |
| Oracle HTF | `oracle_htf.py` (already exists, needs wiring) |
| Remi reviewer | `tak_scanner_v4.py` (RemiReviewer class, needs repositioning) |
| April supervisor | `tak_scanner_v4.py` or new `april_supervisor.py` |
| Worker snapshot route | `server.py` (needs `/snapshot` endpoint) |
| Position manager | `scheduler.py` (needs stale filter) |

---

**END OF ROADMAP**  
Execute tasks 1-11 in order. After completion, the stack will be canonical-ready and live-deployable.
