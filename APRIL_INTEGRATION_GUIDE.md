# April Council Integration Guide

## Status

✅ **council.py created** — April decision logic is ready  
❌ **Feed integration pending** — Scanner needs to emit `april_view` to live terminal

---

## Problem Identified

The JHL Live Terminal has an "April Mode" panel that displays `—` (dash) because:

1. **UI is ready**: Terminal reads `snap.audit.april_view.council_mode` or `snap.april_view.council_mode`
2. **Data contract missing**: Scanner doesn't publish `april_view` object yet
3. **Feed is stale**: No live April status reaching the terminal

---

## Solution

### Step 1: Import council module in scanner

In the file that builds the signal bus snapshot (likely `# order_executor_v2.py`, `tak_scanner_v3.py`, or similar main scanner file), add:

```python
from council import build_council_assessment
```

### Step 2: Build April assessment after scan completes

After the scanner finishes processing all pairs and has:
- `live_signals` list
- `regime_map` dict  
- `fg_score` int (Fear & Greed score)
- `fg_label` str (e.g., "Extreme Fear", "Greed")

Add this code:

```python
# Build April Council assessment
april_view = build_council_assessment(
    signals=[s for s in live_signals],  # Active/pending signals
    regime_map=regime_map,  # Pair -> regime classification
    fg_score=fg_score,  # Fear & Greed score
    fg_label=fg_label   # Fear & Greed label
)
```

### Step 3: Add april_view to bus snapshot

When building the final `signal_bus.json` or canonical snapshot dict, add:

```python
bus_snapshot = {
    "generated_at": datetime.utcnow().isoformat() + 'Z',
    "signals": [s.to_dict() for s in live_signals],
    "regime_map": regime_map,
    "fear_greed": {"score": fg_score, "label": fg_label},
    
    # ADD THIS:
    "april_view": april_view,  # Council mode, status_code, affected_bots
    
    "audit": {
        # existing audit fields...
        # ALTERNATIVE: nest april_view inside audit if preferred
        # "april_view": april_view
    }
}
```

**Choose ONE location:**
- Top-level: `snap.april_view.council_mode`
- Inside audit: `snap.audit.april_view.council_mode`

(Terminal already checks both paths.)

### Step 4: Write to bus and push to worker

Ensure the updated snapshot with `april_view` is:
1. Written to `signal_bus.json`
2. Pushed to Railway worker endpoint

Example:
```python
# Write local bus
with open(SIGNAL_BUS_PATH, 'w') as f:
    json.dump(bus_snapshot, f, indent=2)

# Push to worker
response = requests.post(WORKER_URL, json=bus_snapshot)
logger.info(f"Worker push: {response.status_code}")
```

---

## Expected Behavior After Integration

### Normal Operations
```
APRIL MODE: NORMAL
```
- Green text
- All bots operating within regime parameters

### Stand Down
```
APRIL MODE: STAND_DOWN
```
- Red text
- Status code shown (e.g., "GIMBA_IN_EXTREME_FEAR")
- Affected bots listed

### Time to Hunt
```
APRIL MODE: TIME_TO_HUNT  
```
- Gold text
- Optimal market conditions detected

---

## Files Modified

1. ✅ **council.py** — Created (April decision logic)
2. ⏳ **Main scanner file** — Need to add:
   - Import `build_council_assessment`
   - Call it after scan
   - Add `april_view` to snapshot
3. ✅ **jhl-live-terminal.html** — Already wired to consume `april_view`

---

## Testing

After integration, check the live terminal:

1. **In Extreme Fear with Gimba signals**: Should show `STAND_DOWN / GIMBA_IN_EXTREME_FEAR`
2. **In Greed with trending pairs**: Should show `TIME_TO_HUNT / OPTIMAL_CONDITIONS`
3. **Normal mixed conditions**: Should show `NORMAL`

---

## Next Action

**Find the scanner file that writes `signal_bus.json`** and apply Steps 1-4 above.

Likely candidates:
- `# order_executor_v2.py` (main loop)
- `tak_scanner_v3.py` or `tak_scanner_v4.py`
- Any file with `json.dump(bus_snapshot, ...)` or similar bus write logic
