# Position Tracking Patch

## Problem

Positions in the JHL Live Terminal are stale because `# order_executor_v2.py`:
- ✅ Reads positions from `signal_bus.json`
- ✅ Calculates position sizing
- ❌ **Never writes positions back to the bus**

The executor logs "ORDER QUEUED" but positions array never updates.

---

## Solution

Add position tracking to update the bus when signals are executed.

### Step 1: Add positions list to track active trades

At the top of the main execution loop in `# order_executor_v2.py`, after loading signals:

```python
# After load_signals_from_bus()
signals = load_signals_from_bus()

# ADD THIS: Load current positions from bus
payload = json.loads(SIGNAL_BUS_PATH.read_text() or "{}")
positions = payload.get("positions", [])
logger.info(f"Loaded {len(positions)} existing positions from bus")
```

### Step 2: Track position when order is queued

In the `iter_accounts()` function where it logs "ORDER QUEUED":

```python
if execute:
    accounts_in_signal += 1
    logger.info(" %s — ORDER QUEUED (exchange integration Phase 2)", account["name"])
    
    # ADD THIS: Track position
    position = {
        "pair": sig["pair"],
        "account": account["name"],
        "side": sig.get("side", "LONG"),
        "entry": sig.get("entry"),
        "stop": sig.get("stop"),
        "target": sig.get("target"),
        "size_usd": sizing["risk_dollars"],
        "units": sizing["units"],
        "status": "PENDING",
        "opened_at": datetime.utcnow().isoformat() + 'Z',
        "signal_id": sig.get("id") or f"{sig['pair']}_{int(datetime.utcnow().timestamp())}"
    }
    positions.append(position)
    logger.info(f" → Added position: {position['pair']} on {account['name']}")
```

### Step 3: Write positions back to bus

At the end of the main loop (after all signals processed), update the bus:

```python
# After processing all signals
# ADD THIS: Write updated positions back to bus
if positions:
    payload["positions"] = positions
    SIGNAL_BUS_PATH.write_text(json.dumps(payload, indent=2))
    logger.info(f"✓ Updated bus with {len(positions)} positions")
```

### Step 4: Add position cleanup logic (optional)

To prevent stale positions from accumulating, add cleanup for old positions:

```python
# Before writing positions, clean up old ones
from datetime import timedelta

MAX_POSITION_AGE_HOURS = 72  # Remove positions older than 3 days
now = datetime.utcnow()

active_positions = []
for pos in positions:
    opened = datetime.fromisoformat(pos["opened_at"].replace('Z', '+00:00'))
    age_hours = (now - opened.replace(tzinfo=None)).total_seconds() / 3600
    
    if age_hours < MAX_POSITION_AGE_HOURS:
        active_positions.append(pos)
    else:
        logger.info(f"Removing stale position: {pos['pair']} ({age_hours:.1f}h old)")

positions = active_positions
```

---

## Expected Behavior After Patch

### Before
```json
{
  "signals": [...]  ,
  "positions": []  // Always empty
}
```

### After  
```json
{
  "signals": [...],
  "positions": [
    {
      "pair": "AAVE",
      "account": "Prop 1",
      "side": "LONG",
      "entry": 91.39,
      "stop": 87.7408,
      "target": 98.6884,
      "size_usd": 250.0,
      "units": 68.86,
      "status": "PENDING",
      "opened_at": "2026-07-19T16:30:00Z",
      "signal_id": "AAVE_1721408400"
    }
  ]
}
```

### In Terminal
Positions will now show live in the UI instead of being empty/stale.

---

## Files to Modify

1. **# order_executor_v2.py** — Add position tracking in main loop

---

## Testing

1. Run the scanner to generate signals
2. Run `# order_executor_v2.py` to process signals
3. Check `signal_bus.json` — should contain `positions` array
4. Check JHL Live Terminal — positions should appear

---

## Notes

- This is **Phase 1** position tracking (logging only, no real exchange orders)
- When exchange integration is added in Phase 2, update `status` field:
  - `PENDING` → `FILLED` → `CLOSED`
- Consider adding position P&L tracking when live prices are available
