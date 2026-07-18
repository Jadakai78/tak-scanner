# JHL Holdings — Gimba Handoff Note
**Date:** Saturday July 18, 2026 — 7:15 AM CDT  
**Thread:** https://www.perplexity.ai/computer/tasks/b48608c7-29c3-423f-8e45-af80fedbed14  
**Written by:** Gimba (this thread) for continuity if credits run out or session drifts  

---

## WHO YOU ARE TALKING TO
Jason Warr | JHL Holdings LLC | Chicago IL  
blazing0478@gmail.com + jasonrwarr@outlook.com (ALWAYS BOTH — hard rule)  
GitHub: `Jadakai78/tak-scanner` (private, flat file structure, no packages)  
Railway: `ravishing-possibility` → service `tak-scanner` → `python server.py` → port 5000  
Live URL: `tak-scanner-production.up.railway.app` OR `tak-scanner-production-013f.up.railway.app`  

---

## OPERATING PRINCIPLES (NEVER FORGET)
- **Scientific method always** — prove ourselves wrong, not right
- **Manufacturing model** — one step at a time, both sides approve before moving forward
- **Gimba thread = all building.** Admin tabs = research/analysis only. One source of truth.
- **Casino model** — first loser = done, no exceptions
- **Shop like a poor person** — cheaper is always better
- **Build don't buy** — never pay for something we can create
- **Only write files that need changing** — not the whole folder
- **Share zip files** when delivering code
- **Two numbers in emails** — Jason's autistic preference, makes him feel right at home

---

## CURRENT SYSTEM STATE (as of July 18 2026)

### What's Working
- JHL Live Terminal is LIVE at Railway URL
- RTS Sniper running every 10 min — `rts_last_scan` is fresh/current
- Alert stack: Pushover + Telegram + Yahoo email wired and tested
- Council window in feed: April / Remi / Consensus panels rendering
- Feed reads from `signal_bus.json` via `/api/signals`

### What Was Fixed This Session
| Commit | File | Fix |
|---|---|---|
| `9b3c9ce` | `s2_trend_rider.py` | Dynamic SL/TP — ATR-tightened stop, nearest swing TP, 3×ATR fallback |
| `77aedfad` | `jhl-live-terminal.html` | Council panel wired to `audit.april_view` + Remi aggregate from signals |
| `614f55df` | `jhl-snapshot-adapter.module.js` | Stale `last_scan` detection — falls back to `rts_last_scan`, exposes `scanner_stale` flag |
| `5be892f3` | `jhl-live-terminal.html` | Shows `⚠ STALE` badge on Last Scan KPI when main scanner >2h old |
| `4edb6ec` | `pair_universe.py` | Workers 8→3, retry+backoff on 429/timeout, rate sleep 0.5→1.0s |
| `5350cee` | `tak_scanner_v4.py` | Writes both `f_g` and `fg` keys, adds heartbeat `last_scan` stamp at scan start |
| `e09f7ef` | `server.py` | `/api/position/execute`, `/api/position/reject`, `/api/kraken/status`, `/api/kraken/positions` + Kraken bot daemon thread |
| `e44b2fe` | `jhl-live-terminal.html` | Execute/Reject buttons on signal cards, Kraken tab with bot status + open trades + cycle log |

### Known Remaining Issues
1. **Main scanner stale** — `last_scan` frozen at July 7. Root cause: Kraken rate limiting killed most OHLC fetches (only 11/120 pairs completed). The `pair_universe.py` fix should resolve this on next scan cycle. Watch for `active_pairs` to jump from 11 → 80-120.
2. **`action_state` is None** in bus — `build_bus_payload` doesn't map this from v2 scoring output
3. **`intent` field missing** — bus never writes it, feed shows `—` for Intent
4. **S3 GimbaVolatile + S5 EMA Cross** — still have hardcoded `× 2.0` TP (same bug we fixed in S2). Next session fix these.
5. **Conviction display** — bus sends `0.8394` (0-1 scale), should display as `83.9` not `0.8`
6. **Regime map** — bus sends dict `{pair: regime}`, feed adapter expects array `[{pair, regime}]`
7. **Outlook `OUTLOOK_APP_PASSWORD`** — still needs to be set in Railway env vars (ravishing-possibility service)

---

## THE BIG PICTURE — WHERE THIS IS GOING

### Two Income Streams
| Stream | Purpose | Status |
|---|---|---|
| **Prop accounts** | Frontend income — daily cash flow | Live feed working, Execute/Reject wired |
| **Kraken bot $400** | 401K — long-term compounding | Bot built, needs capital + config flip |

### Council Architecture (LOCKED — do not change)
**Council = April + Remi ONLY.** Full stop.
- **April** — Field General. STAND_DOWN / TIME_TO_HUNT / NORMAL.
- **Remi** — Signal Gatekeeper. Gates every signal before the feed.

**Gimba Volatile Bot** — Standalone autonomous SOL/USD scalper. Fires in VOLATILE regime. Has its own KNN brain trained on volatility data. Does NOT feed the main scanner. Does NOT vote in council.

**Gimba Range Bot** — Standalone autonomous SOL/USD chop scalper. BB+RSI+KNN. Fires only when `chop_label = REAL_CHOP`. Has its own separate KNN brain. Does NOT feed the main scanner. Does NOT vote in council.

Both Gimba bots run their own loop → manage their own Kraken trades when capital arrives. Separate income streams. No cross-wiring with TAK scanner ever.

RTS engines (BOS/CHOCH/ZONE/LIQ/DELTA/BOTTLE) are specialists in the TAK scanner — they ARE primary signals, not council members. ATTACK-intent RTS signals with conviction ≥75 get promoted into the live feed.

---

### Kraken Bot Architecture (NOT YET BUILT — waiting for capital)
Jason is expecting a loan. When money arrives → $400 into Kraken box.

**The vision:** Fully autonomous. No signals fed to Jason. No human in the loop.  
Council/April/Remi run as self-contained decision unit.  
Bot enters AND exits on its own.  
Jason receives **dispatches** (alerts), not requests for permission.

**Flow:**
```
Price Action → Specialists generate candidate
      ↓
Remi gates (CLEAN or CAUTION)
      ↓
Council adjudicates (live / caution / kill)
      ↓
April checks field (NORMAL / TIME_TO_HUNT / STAND_DOWN)
      ↓
Bot enters automatically if all three clear
      ↓
Position health + trap detector watch continuously
      ↓
Auto-exit if health drops BLACK or trap fires
      ↓
Alert to Jason — "ENTERED AAVE LONG" or "COUNCIL KILL — TRAP"
```

### Dynamic Timeframe Structure (AGREED, NOT YET BUILT)
**Jason's spec:** Don't hardcode timeframes. Let the bot determine what looks good.  
**Agreed implementation:** Regime-driven timeframe selection — principled, not cherry-picking.

```
TREND regime   → 4H (confirmed structure, fewer false breaks)
RANGE regime   → 1H (tighter cycles, mean reversion faster)  
FEAR/VOLATILE  → 15M (Gimba territory, quick scalps)
DEAD           → skip entirely
```

Each specialist already declares `REQUIRED_REGIMES`. The timeframe resolver maps regime → interval. Position health polls at entry timeframe cadence, not fixed 60s.

**What needs to be built when capital arrives:**
1. Timeframe resolver (regime → interval map)
2. Kraken bot entry logic using specialists + council gate (fully autonomous)
3. Position monitor — continuous price polling against open positions
4. Auto-exit when health goes BLACK or trap fires
5. Alert on every action (entry + exit) — Pushover + Telegram + email

---

## SCORING SYSTEM (canonical — do not change without Jason's approval)
- 8 criteria, 3 buckets: Core 40% / Defensive 30% / Offensive 30%
- conviction 0-100 IS the grade. No letter grades.
- ≥88 = Sammy (S-tier). ≥75 = A-tier.
- Bonuses up to ×3
- CLICK requires: def≥0.72 + off≥0.68 + conviction≥75
- REJECT if trap≥0.75 or both scores weak
- TrapDetector: HARD=0.75, CAUTION=0.55
- Sammy only alerts: conviction ≥88
- Bonus ×3 when S-grade + trap_score ≤0.40 (clean Sammy)

---

## ALERT ROUTING (canonical)
| Grade | Pushover | Telegram | Yahoo | Outlook |
|---|---|---|---|---|
| S (≥88/Sammy) | popup priority 1 | entry block | full breakdown | full breakdown |
| A (≥75) | popup | entry block | — | — |
| KILL | popup priority 1 | entry block | full breakdown | full breakdown |
| B/C | silent | silent | silent | silent |

Pushover format: `🟢 S: SOLUSD LONG | S3Gimba | Conv 91 | ATTACK`  
Telegram: entry block — pair, bias, engine, Entry/SL/TP, R:R, conviction, regime, sizing  
Email: full breakdown — all scoring, microstructure, MTF, delta bias  

---

## CREDENTIALS (from file.env)
```
KRAKEN_API_KEY=CCIgtpV5msWRw4pSq2hkDWlBQXiLJhyKo+9KWgctxCHQBNm3Ec6SI4IE
KRAKEN_API_SECRET=qVuJgdKp0BI1hdiQhpMlatzzuYsnHd7cYoxgCgdWQlDVqEuzU6bV5mHdd5MGir1HLDPl1vlZOFFwBfOmqkwMcQ==
PUSHOVER_USER_KEY=u4v2rgci4vm95ezqx4czssz2t2du6a
PUSHOVER_API_TOKEN=a144kiwuifpzpjmbpjfei63dvyqfuu
TELEGRAM_TOKEN=8860741830:AAGiccCbk4dzoTq97gWIIykZVunDvkkl6ys
TELEGRAM_CHAT_ID=7733126931
YAHOO_SMTP_USER=tolow47@yahoo.com / APP_PASSWORD=xvuxwkffiuhigyed
CF_ACCOUNT_ID=ea17be7c9b13c5f9c1fec378a44e9e39
CF_KV_NS=e93558412bde4922828325e714bc44d8
CF_KV_URL=https://api.cloudflare.com/client/v4/accounts/ea17be7c9b13c5f9c1fec378a44e9e39/storage/kv/namespaces/e93558412bde4922828325e714bc44d8/values/signal_bus
OUTLOOK_APP_PASSWORD → SET IN RAILWAY ENV VARS (NOT YET DONE)
```

---

## INFRASTRUCTURE
- **GitHub**: `Jadakai78/tak-scanner` — flat file structure, Railway auto-deploys on push
- **Railway**: `ravishing-possibility` → `tak-scanner` → `python server.py` → port 5000
- **CF Worker**: `jhl-signal-bus.blazing0478.workers.dev`
- **Scheduler**: runs inside `server.py` as daemon thread — no separate service needed
- **Kraken bot**: also runs inside `server.py` as daemon thread (as of this session)
- **Dead project**: `tranquil-flow` on Railway — Jason should delete manually

---

## SPECIALIST REGISTRY (14 total)
S1 S2Trend S3Gimba S4Mean S5EMA S6Reversal S7Range S9Cap  
RTS_LIQ RTS_BOS RTS_CHOCH RTS_ZONE RTS_DELTA RTS_BOTTLE  

S3 and S5 still have hardcoded 2R TP — fix next session.

---

## ACTIVE CRONS
| ID | Name | Schedule |
|---|---|---|
| 230967c4 | Daily 9AM News Briefing | Daily 9AM CDT |
| d89bf087 | April 2AM Ops Briefing | Daily 2AM CDT |
| cf21c98e | Position Monitor | Every 4H |
| dd5c31f7 | Weekday Bot Report | Mon-Fri 8AM CDT |

---

## PROP ACCOUNT STATUS (as of session start)
| Account | Status |
|---|---|
| Eval 4 $25K DRAGON | FAILED |
| Starter 3 $10K | FAILED |
| Starter 2 $10K | BARELY ALIVE |
| Eval 1 $5K | BARELY ALIVE |

---

## NEXT SESSION PRIORITY LIST
1. Confirm scanner is healthy — check `active_pairs` count (should be 80-120, not 11)
2. Fix S3 GimbaVolatile + S5 EMA Cross hardcoded TP (same fix as S2)
3. Fix `action_state`, `intent`, `mtf` field mismatches in `build_bus_payload`
4. Fix conviction display — show `83.9` not `0.8`
5. Fix regime map — dict → array conversion in adapter
6. Set `OUTLOOK_APP_PASSWORD` in Railway env vars
7. **When capital arrives:** Build autonomous Kraken bot with dynamic timeframe + council gate

---

## JASON'S RULES (permanent)
- Pull profits daily up to $325. No compounding — cash flow priority
- Waterfall: $167 overhead + $25 credits = $192 floor → Jason capped at $200 → JHL $100 → max $325/day
- 10% of every payout feeds Kraken 401K
- Shorts ENABLED on Breakout and Kraken
- No mid-drive trading decisions — ever
- CAUTION rules: Neg C1 → CUT IMMEDIATELY. Flat C2 → CUT. 2 candles max
- Feed: S and A grades ONLY. B/C stay in logs — never touch the phone
- BOS retest only — never the BOS candle itself
- Missed signal = skip, never chase
- SMC as permanent foundation
- 24/7 operation — quiet hours REMOVED
