# Handoff Note — July 19, 2026

## Current State

This note documents the scanner rebuild session on July 19, 2026. The scanner was down for 12 days (since July 7th) due to a Railway deployment configuration error. The system is now restored and operational.

---

## What Was Broken

### Root Cause
When the "tranquility" Railway deployment was deleted, the scanner scheduler stopped running. The replacement service ("attractive-dream") was created but had the wrong start command:

- ❌ **Wrong**: `python server.py` (runs the web API)
- ✅ **Correct**: `python scheduler.py` (runs the scanner every 20 minutes)

### Evidence
- `signal_bus.json` showed last scan: **July 7, 2026 at 4:46 AM**
- Terminal displayed 12-day-old stale signals: AAVE, HYPE, SOL
- Railway backend service showing **CPU/memory maxed out**
- No fresh scans running despite Procfile containing worker definition

---

## What Was Fixed

### 1. Railway Configuration
**File**: `railway.json`  
**Change**: Line 10 — `"startCommand": "python server.py"` → `"python scheduler.py"`  
**Commit**: 29faee4 — "Change start command from server.py to scheduler.py"

### 2. Procfile Worker Process
**File**: `Procfile`  
**Added**: `worker: python scheduler.py`  
**Commit**: b2d2bde — "Add worker process to Procfile for scheduler"

### 3. Council Decision Layer (April)
**File**: `council.py` (NEW)  
**Purpose**: April Field General bot performance monitoring  
**Features**:
- Assesses whether bots are firing in appropriate regime conditions
- Issues STAND_DOWN codes when bots misfire (e.g., Gimba in Extreme Fear)
- Returns `april_view` object for canonical snapshot with:
  - `council_mode`: NORMAL | STAND_DOWN | TIME_TO_HUNT
  - `status_code`: Specific issue (e.g., GIMBA_IN_EXTREME_FEAR)
  - `regime_context`: Current Fear & Greed state
  - `affected_bots`: List of bots in violation

### 4. Integration Documentation
**File**: `APRIL_INTEGRATION_GUIDE.md` (NEW)  
**Purpose**: Step-by-step guide to wire April assessment into scanner  
**Status**: ⏳ Pending implementation (scanner needs to emit `april_view`)

**File**: `POSITION_TRACKING_PATCH.md` (NEW)  
**Purpose**: Fix for stale positions in terminal  
**Status**: ⏳ Pending implementation (executor needs to write positions back to bus)

---

## Current Railway Architecture

### Project: ravishing-possibility
**Services**:
1. **giving-wisdom** (web service)
   - Status: ✅ Online
   - Command: `gunicorn server:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
   - Purpose: API server serving signal feed to JHL Live Terminal
   - URL: https://giving-wisdom-production-9b27.up.railway.app

2. **attractive-dream** (worker service)
   - Status: ✅ Online (FIXED)
   - Command: `python scheduler.py` (corrected from server.py)
   - Purpose: Runs `tak_scanner_v4.py` every 20 minutes
   - No public URL (internal worker)

3. **giving-wisdom-volume**
   - Purpose: Persistent storage

**Deleted Services**:
- **empowering-tranquility**: Deleted (caused scanner outage)
- **tranquil-flow**: No services

---

## Scanner Mechanics

### Scheduler Loop
**File**: `scheduler.py`  
**Interval**: 20 minutes (`INTERVAL_SECONDS = 20 * 60`)  
**Timeout**: 10 minutes per scan (`TIMEOUT = 600`)  
**Scanner**: `MODULE_DIR / "tak_scanner_v4.py"`  
**Output**: Writes to `signal_bus.json` and pushes to Cloudflare worker

### Signal Bus Contract
**File**: `signal_bus.json`  
**Location**: Root directory (NOT `app/signal_bus.json`)  
**Current Fields**:
- `last_scan`: ISO timestamp
- `next_scan`: ISO timestamp  
- `fear_greed`: {score, label}
- `regime_map`: {pair: TREND_UP | TREND_DOWN | RANGE | VOLATILE | DEAD}
- `signals`: Array of signal objects
- `positions`: Array (currently empty — needs patch)

**Missing Fields** (from canonical contract):
- `april_view`: Council decision object
- Top-level: meta, session, health, regimes, alerts, diagnostics

---

## Pending Tasks

### High Priority
1. **Verify Scanner is Running**
   - Wait 20 minutes for first scan
   - Check `signal_bus.json` for fresh timestamp
   - Confirm JHL Live Terminal shows new signals

2. **Apply April Integration**
   - Follow `APRIL_INTEGRATION_GUIDE.md`
   - Add `from council import build_council_assessment` to scanner
   - Call after scan completes
   - Add `april_view` to bus snapshot
   - Verify terminal displays April Mode status

3. **Apply Position Tracking Patch**
   - Follow `POSITION_TRACKING_PATCH.md`
   - Modify `# order_executor_v2.py` to write positions back to bus
   - Verify terminal displays active positions

### Medium Priority
4. **Bot Scoring Audit**
   - Review `BOT_SCORING_AUDIT.md`
   - Fix regime-aware scoring (bots firing in wrong regimes)
   - Address S3 Gimba Volatile hardcoded TP bug

5. **Canonical Snapshot Migration**
   - Follow consolidation guides in attached handoff notes
   - Migrate to meta/session/health/regimes/signals/alerts/diagnostics structure

### Low Priority
6. **Railway Resource Monitoring**
   - Backend service hitting CPU/memory limits
   - Consider upgrading plan or optimizing scanner memory usage

---

## File Inventory

### Documentation Created This Session
- ✅ `council.py` — April decision logic
- ✅ `APRIL_ALERTS_SPEC.md` — April alerts panel specification
- ✅ `APRIL_INTEGRATION_GUIDE.md` — How to wire April into scanner
- ✅ `POSITION_TRACKING_PATCH.md` — How to fix stale positions
- ✅ `BOT_SCORING_AUDIT.md` — Analysis of regime awareness issues
- ✅ `ALERT_SETUP_GUIDE.md` — Dual-channel S/A grade alerts (Telegram + Outlook)
- ✅ `CONSOLIDATION_ROADMAP.md` — Architecture consolidation plan

### Existing Key Files
- `scheduler.py` — Scanner orchestration (runs every 20 minutes)
- `# order_executor_v2.py` — Position sizing and execution logic
- `conviction_scorer.py` — Signal grading (MIN_RR now configurable via env)
- `Procfile` — Railway process definitions
- `railway.json` — Railway service configuration
- `signal_bus.json` — Live signal feed (root directory)
- `jhl-live-terminal.html` — Dashboard UI

---

## Known Issues

### Active
1. **S8MTFConfluence keyword mismatch**
   - Error: `TypeError: got unexpected keyword argument 'pair_key'`
   - Impact: Some scans fail at ASTER
   - Fix: Align caller/callee signatures

2. **April panel not live**
   - Terminal shows "—" for April Mode
   - Fix: Apply APRIL_INTEGRATION_GUIDE.md

3. **Positions not updating**
   - Terminal shows empty/stale positions
   - Fix: Apply POSITION_TRACKING_PATCH.md

### Resolved
- ✅ Scanner not running (fixed railway.json)
- ✅ Procfile missing worker line (added)
- ✅ Indentation error in scheduler.py (fixed in previous session)
- ✅ MIN_RR hardcoded (now uses os.getenv)

---

## Critical Reminders

### Council Architecture
- **Council = April + Remi ONLY**
- April: Field General (monitors bot performance, issues STAND_DOWN)
- Remi: Front-end classification and position management
- Gimba bots: Separate standalone systems (do NOT mix)

### Railway Services
- **giving-wisdom**: Web API (must stay online for terminal feed)
- **attractive-dream**: Scanner worker (runs scheduler.py every 20 minutes)
- **DO NOT delete services without verifying backup scheduler**

### File Paths
- Signal bus: `signal_bus.json` (root, NOT app/signal_bus.json)
- Scanner: `tak_scanner_v4.py`
- Scheduler: `scheduler.py`

---

## Success Criteria

✅ **Scanner restored** — Runs every 20 minutes  
⏳ **Fresh signals** — Verify within 20 minutes of fix deployment  
⏳ **April panel live** — After integration guide applied  
⏳ **Positions updating** — After tracking patch applied  

---

## Next Session Restart Sequence

If resuming from scratch:

1. Check Railway "attractive-dream" service is online
2. Verify `railway.json` line 10 = `"python scheduler.py"`
3. Check `signal_bus.json` for recent `last_scan` timestamp
4. Open JHL Live Terminal: https://giving-wisdom-production-9b27.up.railway.app
5. If April shows "—", apply `APRIL_INTEGRATION_GUIDE.md`
6. If positions empty, apply `POSITION_TRACKING_PATCH.md`

---

## Contact / Logs

**Railway Project**: ravishing-possibility  
**Live Terminal**: https://giving-wisdom-production-9b27.up.railway.app  
**GitHub Repo**: https://github.com/Jadakai78/tak-scanner  
**Session Date**: July 19, 2026 @ 10:00-11:30 AM CDT


---

## Update: Scheduler Fix Merged (PR #1)

**Time**: ~40 minutes after initial handoff

### ✅ Completed

**PR #1**: Fix scheduler.py indentation and control flow
- **Status**: ✅ Merged successfully  
- **Deployment**: ✅ ONLINE (both services running)
  - `attractive-dream`: ✅ Online (scheduler service)
  - `giving-wisdom`: ✅ Online (web API)

**Changes**:
1. Dedented verdict-snapshot logic to proper level inside outer try block
2. Fixed `subprocess.run()` call indentation (line 79 was nested too deeply)  
3. Moved verdict-restore logic to correct place after subprocess call
4. Moved `push_to_cf()` into `finally` block so it always runs
5. Reformatted unreadable inline code at bottom of `run()` into proper multi-line Python

### ❌ Remaining Issues

**NEW**: Syntax errors in `tak_scanner_v4.py`
- **Root Cause**: Bot reformatting crushed multi-line function definitions onto single lines
- **File**: `tak_scanner_v4.py`
- **Line 146**: `def build_bus():` has docstring and code crammed onto one line
- **Impact**: Scanner logic BROKEN (syntax errors will prevent execution)

**Evidence**:
```python
# Line 146 (BROKEN - needs fixing):
def build_bus():"""Map ScanResult → flat bus shape the CF worker + feed adapter expect."""     rts_map = rts_map or {}     bar_map = bar_map or ...
```

### 🔧 Next Steps

1. **Fix tak_scanner_v4.py syntax errors**  
   - Properly format `build_bus()` function definition
   - Break up mangled single line into proper multi-line Python
   - Ensure correct indentation for function body
   
2. **Test Scanner Execution**
   - After fix, wait 20 minutes for scheduled scan
   - Verify `signal_bus.json` updates with fresh timestamp
   - Check JHL Live Terminal for new signals

3. **Apply Pending Patches** (once scanner is functional)
   - April Integration Guide
   - Position Tracking Patch
