# April Alerts Panel — Missing Infrastructure Spec

**Date:** July 19, 2026  
**Problem:** April is supposed to issue status codes when bots aren't performing, but there's no alerts panel in the feed to show them.

---

## The Gap

### Architecture Says:
Per the handoff docs, **Council = April + Remi only**. Two members:
- **Remi**: Gates signals (caution/cut/kill)
- **April**: Signs off, issues status codes when market conditions don't suit bots

### Current State:
Looking at your live terminal and the structure breakdown:
- ✅ Signals panel exists (shows AAVE, HYPE, SOL)
- ✅ RTS Traps counter exists (shows 0)
- ❌ **Alerts panel is completely missing**
- ❌ No way to see April's status codes
- ❌ No "APRIL_MODE" indicator

### What Should Exist:
According to the canonical snapshot structure:
```json
{
  "meta": {...},
  "session": {...},
  "health": {...},
  "regimes": {...},
  "signals": [...],
  "alerts": [        // ← MISSING
    {
      "timestamp": "2026-07-19T14:26:17Z",
      "severity": "WARNING",
      "source": "APRIL",
      "code": "NEEDS_REVISION",
      "message": "S1 Sniper firing in RANGE regime — needs regime multiplier",
      "affected_bots": ["S1"],
      "action_required": "Reduce conviction or stand down in non-TREND regimes"
    },
    {
      "timestamp": "2026-07-19T14:20:03Z",
      "severity": "INFO",
      "source": "APRIL",
      "code": "NOT_SUITABLE",
      "message": "Extreme Fear with 0 signals — market conditions not suitable",
      "market_context": "FEAR regime, FG=25"
    }
  ],
  "diagnostics": [...]
}
```

---

## April's Alert Codes

### Status Codes April Should Issue:

| Code | Severity | Meaning | When Issued |
|------|----------|---------|-------------|
| `APPROVED` | INFO | Signal passed all checks | Normal operation |
| `NEEDS_REVISION` | WARNING | Bot firing in wrong regime | Bot-regime mismatch detected |
| `NOT_SUITABLE` | WARNING | Market conditions don't suit any bots | Extreme Fear + no signals, Dead regime |
| `STAND_DOWN` | CAUTION | Multiple bots underperforming | 3+ signals killed in session |
| `REGIME_MISMATCH` | WARNING | Specific bot-regime conflict | S1/S2 in RANGE, RTS in TREND |
| `LOW_CONVICTION` | INFO | Signals passing but weak | Conviction 75-80 range |
| `HIGH_TRAP_RISK` | CAUTION | Trap detector flagging heavily | Trap score >0.70 |

### Priority Levels:
- 🔴 **CRITICAL**: System errors, S8 MTF failures
- 🟡 **WARNING**: Regime mismatches, needs revision
- 🔵 **INFO**: Normal status updates, market context
- ⚪ **DEBUG**: Diagnostic-only data (not shown in UI)

---

## Where April Logic Should Live

### Current Architecture:
```
Scanner Core
  ├─ S1-S9 Specialists (generate signals)
  ├─ RTS Engines (liquidation hunts)
  └─ Regime Classifier (market state)
           ↓
      Remi Gates (caution/cut/kill)
           ↓
    April Signs Off  ← NEEDS IMPLEMENTATION
           ↓
     Signal Bus
           ↓
      Live Feed
```

### Implementation Path:

#### 1. **Add April Council Module** (`april_council.py`)

Create a new module that:
- Receives post-Remi signals
- Checks regime alignment (from BOT_SCORING_AUDIT.md logic)
- Issues alert codes
- Stamps approval or raises warnings

```python
# april_council.py

import logging
from typing import Dict, List, Any
from datetime import datetime

logger = logging.getLogger("april_council")

class AprilCouncil:
    """
    Final council member - signs off on signals and issues market alerts.
    """
    
    def __init__(self):
        self.alerts = []
        self.session_stats = {
            "signals_approved": 0,
            "signals_flagged": 0,
            "regime_mismatches": 0
        }
    
    def review_signal(self, signal: Dict[str, Any], regime: str) -> Dict[str, Any]:
        """
        April's final review - check if bot is suitable for current regime.
        """
        bot = signal.get("signal_name", "")
        conviction = signal.get("conviction", 0)
        
        # Regime alignment check
        if self._is_regime_mismatch(bot, regime):
            self._issue_alert(
                severity="WARNING",
                code="REGIME_MISMATCH",
                message=f"{bot} firing in {regime} regime - not optimal",
                affected_bots=[bot]
            )
            self.session_stats["regime_mismatches"] += 1
            signal["april_status"] = "NEEDS_REVISION"
            signal["april_note"] = f"Bot not suited for {regime} regime"
        
        elif conviction < 80:
            signal["april_status"] = "LOW_CONVICTION"
            signal["april_note"] = f"Weak conviction ({conviction})"
        
        else:
            signal["april_status"] = "APPROVED"
            signal["april_note"] = "Signal approved for execution"
            self.session_stats["signals_approved"] += 1
        
        return signal
    
    def review_session(self, signals: List[Dict], regime_state: Dict, fear_greed: Dict) -> None:
        """
        Session-level review - issue market-wide alerts.
        """
        # Check for extreme conditions
        fg_score = fear_greed.get("score", 50)
        fg_label = fear_greed.get("label", "NEUTRAL")
        
        if fg_label == "EXTREME_FEAR" and len(signals) == 0:
            self._issue_alert(
                severity="WARNING",
                code="NOT_SUITABLE",
                message=f"Extreme Fear (FG={fg_score}) with 0 signals - market not suitable",
                market_context=f"{fg_label} regime"
            )
        
        # Check for DEAD regime dominance
        dead_count = sum(1 for r in regime_state.values() if r == "DEAD")
        total_pairs = len(regime_state)
        
        if dead_count / total_pairs > 0.5:
            self._issue_alert(
                severity="CAUTION",
                code="STAND_DOWN",
                message=f"{dead_count}/{total_pairs} pairs in DEAD regime - low activity expected"
            )
    
    def _is_regime_mismatch(self, bot: str, regime: str) -> bool:
        """
        Check if bot is firing in wrong regime per BOT_SCORING_AUDIT.md.
        """
        TREND_BOTS = ["S1", "S2"]
        RANGE_BOTS = ["S3", "S4", "S7", "S10"]
        RTS_BOTS = ["RTS"]
        
        if any(bot.startswith(tb) for tb in TREND_BOTS):
            return regime in ["RANGE", "VOLATILE", "DEAD"]
        
        if any(bot.startswith(rb) for rb in RANGE_BOTS):
            return regime in ["TREND_UP", "TREND_DOWN"]
        
        if "RTS" in bot:
            return regime in ["TREND_UP", "TREND_DOWN"]
        
        return False
    
    def _issue_alert(self, severity: str, code: str, message: str, **kwargs):
        """
        Create an alert entry.
        """
        alert = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "severity": severity,
            "source": "APRIL",
            "code": code,
            "message": message,
            **kwargs
        }
        self.alerts.append(alert)
        logger.info(f"[APRIL {severity}] {code}: {message}")
    
    def get_alerts(self) -> List[Dict[str, Any]]:
        """Return all alerts for this session."""
        return self.alerts
    
    def get_mode(self) -> str:
        """
        Determine April's overall mode based on session stats.
        """
        if self.session_stats["regime_mismatches"] >= 3:
            return "STAND_DOWN"
        elif self.session_stats["signals_approved"] == 0:
            return "MONITORING"
        else:
            return "NORMAL"
```

#### 2. **Wire April into Scanner** (`tak_scanner_v4.py`)

Add April review AFTER Remi gates:

```python
# In tak_scanner_v4.py scan cycle

from april_council import AprilCouncil

april = AprilCouncil()

# After Remi review
for signal in remi_approved_signals:
    regime = regime_map.get(signal["pair"], "UNKNOWN")
    signal = april.review_signal(signal, regime)

# Session-level review
april.review_session(
    signals=final_signals,
    regime_state=regime_map,
    fear_greed=fear_greed_data
)

# Add alerts to signal bus
signal_bus["alerts"] = april.get_alerts()
signal_bus["april_mode"] = april.get_mode()
```

#### 3. **Update Signal Bus Schema** (`signal_bus_schema.py`)

Add alerts section to canonical snapshot:

```python
CANONICAL_SNAPSHOT = {
    "meta": {...},
    "session": {...},
    "health": {...},
    "regimes": {...},
    "signals": [...],
    
    # NEW: April alerts
    "alerts": [
        {
            "timestamp": "ISO8601",
            "severity": "INFO | WARNING | CAUTION | CRITICAL",
            "source": "APRIL | REMI | SYSTEM",
            "code": "Status code",
            "message": "Human-readable message",
            "affected_bots": ["Optional list"],
            "market_context": "Optional context"
        }
    ],
    
    "april_mode": "NORMAL | MONITORING | STAND_DOWN",
    
    "diagnostics": [...]
}
```

---

## UI Implementation

### Add Alerts Panel to JHL Live Terminal

Update `jhl-live-terminal.html` to add an Alerts section:

```html
<!-- NEW: Alerts Panel -->
<div class="panel" id="alerts-panel">
  <h2>APRIL ALERTS</h2>
  
  <div class="april-mode" id="april-mode">
    <span class="mode-label">MODE:</span>
    <span class="mode-value" id="mode-value">NORMAL</span>
  </div>
  
  <div class="alerts-list" id="alerts-list">
    <!-- Dynamic alerts populate here -->
  </div>
</div>

<style>
.april-mode {
  background: #1a1a2e;
  padding: 12px;
  border-left: 4px solid #00ff88;
  margin-bottom: 16px;
}

.mode-value {
  font-weight: bold;
  color: #00ff88;
}

.mode-value.stand-down {
  color: #ff4444;
}

.mode-value.monitoring {
  color: #ffaa00;
}

.alert-item {
  background: #16213e;
  padding: 12px;
  margin-bottom: 8px;
  border-left: 4px solid #666;
}

.alert-item.warning {
  border-left-color: #ffaa00;
}

.alert-item.caution {
  border-left-color: #ff4444;
}

.alert-item.info {
  border-left-color: #00aaff;
}

.alert-timestamp {
  font-size: 0.85em;
  color: #888;
}

.alert-code {
  font-weight: bold;
  color: #00ff88;
  margin-right: 8px;
}

.alert-message {
  color: #e0e0e0;
}
</style>

<script>
function updateAlertsPanel(snapshot) {
  const alerts = snapshot.alerts || [];
  const aprilMode = snapshot.april_mode || "NORMAL";
  
  // Update mode indicator
  const modeValue = document.getElementById("mode-value");
  modeValue.textContent = aprilMode;
  modeValue.className = `mode-value ${aprilMode.toLowerCase().replace('_', '-')}`;
  
  // Populate alerts
  const alertsList = document.getElementById("alerts-list");
  
  if (alerts.length === 0) {
    alertsList.innerHTML = '<div class="no-alerts">No alerts - all systems normal</div>';
    return;
  }
  
  alertsList.innerHTML = alerts.map(alert => `
    <div class="alert-item ${alert.severity.toLowerCase()}">
      <div class="alert-header">
        <span class="alert-code">${alert.code}</span>
        <span class="alert-timestamp">${new Date(alert.timestamp).toLocaleTimeString()}</span>
      </div>
      <div class="alert-message">${alert.message}</div>
      ${alert.affected_bots ? `<div class="alert-bots">Affects: ${alert.affected_bots.join(', ')}</div>` : ''}
    </div>
  `).join('');
}

// Call in main refresh loop
function refreshFeed() {
  fetch('/api/signals')
    .then(r => r.json())
    .then(snapshot => {
      updateSignalsPanel(snapshot);
      updateAlertsPanel(snapshot);  // NEW
      updateRegimesPanel(snapshot);
      // ...
    });
}
</script>
```

### Visual Layout

```
┌─────────────────────────────────────────────┐
│ JHL Live Terminal                           │
├─────────────────────────────────────────────┤
│ LAST SCAN: 04:26:17  SIGNALS: 3  TRAPS: 0  │
│ APRIL MODE: STAND_DOWN                      │ ← NEW
├─────────────────────────────────────────────┤
│ APRIL ALERTS                                │ ← NEW PANEL
│ ┌─────────────────────────────────────────┐ │
│ │ ⚠️ REGIME_MISMATCH | 10:24:15          │ │
│ │ S1 Sniper firing in RANGE regime        │ │
│ │ Affects: S1                             │ │
│ └─────────────────────────────────────────┘ │
│ ┌─────────────────────────────────────────┐ │
│ │ ⚠️ NOT_SUITABLE | 10:20:03             │ │
│ │ Extreme Fear with 0 signals             │ │
│ └─────────────────────────────────────────┘ │
├─────────────────────────────────────────────┤
│ REGULAR BOT SIGNALS              3          │
│ ┌─────────────────────────────────────────┐ │
│ │ AAVE | LONG | Conv 83.9 | S2 | TREND_UP│ │
│ └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────┘
```

---

## Implementation Checklist

### Backend (Scanner)
- [ ] Create `april_council.py` module
- [ ] Add regime mismatch detection logic
- [ ] Wire April review into scanner after Remi
- [ ] Add `alerts` array to signal_bus.json output
- [ ] Add `april_mode` field to signal_bus.json
- [ ] Test with current live signals (AAVE, HYPE, SOL)

### API Layer
- [ ] Update `/api/signals` to include alerts in response
- [ ] Ensure alerts array is always present (empty [] if none)
- [ ] Test API response shape matches canonical snapshot

### Frontend (JHL Terminal)
- [ ] Add Alerts panel HTML structure
- [ ] Add CSS for alert severity colors
- [ ] Wire `updateAlertsPanel()` function
- [ ] Add APRIL_MODE indicator to header
- [ ] Test with mock alert data
- [ ] Test with live feed

### Testing
- [ ] Generate test alerts manually
- [ ] Verify S1 in RANGE triggers `REGIME_MISMATCH`
- [ ] Verify Extreme Fear + 0 signals triggers `NOT_SUITABLE`
- [ ] Verify alerts show in UI
- [ ] Verify mode changes (NORMAL → STAND_DOWN)

---

## Example Alert Scenarios

### Scenario 1: Regime Mismatch Detected
```json
{
  "timestamp": "2026-07-19T14:26:17Z",
  "severity": "WARNING",
  "source": "APRIL",
  "code": "REGIME_MISMATCH",
  "message": "S1 Sniper + S2 Trend Rider firing in RANGE regime",
  "affected_bots": ["S1", "S2"],
  "action_required": "Implement regime multipliers from BOT_SCORING_AUDIT.md"
}
```

**UI Display:**
```
⚠️ REGIME_MISMATCH | 10:26:17
S1 Sniper + S2 Trend Rider firing in RANGE regime
Affects: S1, S2
```

### Scenario 2: Market Not Suitable
```json
{
  "timestamp": "2026-07-19T14:20:03Z",
  "severity": "INFO",
  "source": "APRIL",
  "code": "NOT_SUITABLE",
  "message": "Extreme Fear (FG=25) with 0 signals - conditions not suitable for any strategy",
  "market_context": "EXTREME_FEAR regime, 124 pairs scanned"
}
```

**UI Display:**
```
ℹ️ NOT_SUITABLE | 10:20:03
Extreme Fear (FG=25) with 0 signals - conditions not suitable
```

### Scenario 3: Stand Down Issued
```json
{
  "timestamp": "2026-07-19T14:30:00Z",
  "severity": "CAUTION",
  "source": "APRIL",
  "code": "STAND_DOWN",
  "message": "3 regime mismatches detected this session - recommend parameter review",
  "session_context": "3/3 signals flagged for regime issues"
}
```

**UI Display:**
```
🔴 STAND_DOWN | 10:30:00
3 regime mismatches detected - recommend parameter review
APRIL MODE: STAND_DOWN
```

---

## Priority

**HIGH** — This is a missing piece of the council architecture. Without April alerts:
- You can't see when bots are misfiring in wrong regimes
- No visibility into market suitability
- Missing the "needs revision" feedback loop
- Can't tell when April says "stand down"

**Next Steps:**
1. Create `april_council.py` (30 min)
2. Wire into scanner (15 min)
3. Update signal_bus output (10 min)
4. Add UI panel (45 min)
5. Test live (15 min)

**Total: ~2 hours to full implementation**

---

## Summary

**The Problem:** April's alerts exist in concept but not in the feed.  
**The Solution:** Create April council module, wire it post-Remi, add alerts array to signal bus, display in UI panel.  
**The Payoff:** You'll see when bots need regime tuning, when market conditions aren't suitable, and when April says "stand down" — instead of guessing from signal absence.
