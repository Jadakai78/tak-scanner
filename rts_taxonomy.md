# RTS Taxonomy

## Overview
This document defines the RTS (Retail Theft Specialist) family: RTS-LIQ, RTS-CHOCH, RTS-BOS, RTS-ZONE, and RTS-DELTA. Each branch inherits the foundation rules: risk first, mechanical kill levels, mandatory auto-cut, structure as inventory, liquidity over vocabulary, trend as permission, separate Offence/Defence/Trap scores, explicit intent, lanes as risk deployment, feed simplicity, and dead setups disappearing. [file:94][file:96][file:141][web:221]

---

## RTS-LIQ — Liquidity & Forced Flow

### Mission
Identify and classify liquidity pools and sweeps (stop runs, false breaks, and genuine runs) around obvious public levels, then resolve them into ATTACK_TRAP, ATTACK_BREAK, PROBE, WAIT, CUT, or IGNORE with clear kill bands.

### Liquidity Pool Types
- EQH — Equal Highs (buy-side liquidity above clustered highs).
- EQL — Equal Lows (sell-side liquidity below clustered lows).
- PDH — Previous Day High.
- PDL — Previous Day Low.
- PWH — Previous Week High.
- PWL — Previous Week Low.
- BREAKOUT_SHELF_HIGH — Range high breakout shelf.
- BREAKOUT_SHELF_LOW — Range low breakout shelf.
- SWING_HIGH_POOL — Major swing high stop pool.
- SWING_LOW_POOL — Major swing low stop pool.

Each pool type uses ATR-relative tolerance and pivot/shelf detection similar to best-practice SMC indicators for EQH/EQL and PDH/PDL. [web:231][web:233][web:247][web:250][web:251][web:255]

### Core Fields
RTS-LIQ raw output should include:
- liquidity_pool_type
- sweep_side (BUY_SIDE/SELL_SIDE)
- sweep_level
- sweep_displacement
- sweep_type (FAST_WICK / SLOW_ACCEPT)
- reclaim_status (RECLAIMED / ACCEPTED / UNCLEAR)
- reclaim_window_bars
- continuation_status (CONTINUATION_CONFIRMED / CONTINUATION_UNCERTAIN)
- trap_quality
- offence_score
- defence_score
- intent
- kill_level
- auto_cut

### EQH/EQL Rules
Trigger:
- Two or more swing pivots at approximately the same price (ATR-relative tolerance) forming equal highs or equal lows. [web:231][web:247][web:251]
- Price sweeps beyond that band far enough to trigger resting stops and breakout orders. [web:231][web:239][web:242]

Confirmation:
- RECLAIMED (trap): sweep beyond EQH/EQL, then quick rejection and close back inside the prior range.
- ACCEPTED (run): sweep then hold beyond the band; closes and builds value outside.
- UNCLEAR: messy chop with no decisive reclaim or acceptance.

Kill Rules:
- Trap short from EQH: kill slightly above the sweep high.
- Trap long from EQL: kill slightly below the sweep low.
- Break long/short: kill back inside the prior range beyond a reclaim band.

Intent Mapping:
- ATTACK_TRAP when trap_quality and reclaim_status RECLAIMED with strong Offence/Defence.
- ATTACK_BREAK when continuation_status CONFIRMED and acceptance clean.
- PROBE for weaker confirmation or marginal skew.
- WAIT when reclaim_status/continuation_status UNCLEAR.
- CUT when price hits kill_level or confirmation fails inside reclaim_window_bars.
- IGNORE when level is not truly obvious or skew unacceptable. [web:239][web:253][web:256]

### PDH/PDL Rules
Mirrors EQH/EQL with session weight:
- Triggers on sweeps of prior day high/low (and optionally prior week high/low). [web:233][web:250][web:255][web:258]
- Same RECLAIMED/ACCEPTED/UNCLEAR structure.
- Same trap vs run kill logic: stop just beyond sweep for reversal; stop back inside yesterday's range for continuation.

### Breakout Shelves
Trigger:
- Clear horizontal/near-horizontal range high/low tested multiple times.
- Price pushes beyond the shelf band enough to trigger breakout trades and stops. [web:263][web:268][web:270]

Confirmation:
- Trap (ATTACK_TRAP): quick snap back inside the shelf, close inside, rejection wicks, exhausted breakout side.
- Run (ATTACK_BREAK): hold and build value beyond, retest from the other side and continue.

Kill:
- Trap: stop just beyond the sweep extreme.
- Run: stop back inside the shelf range beyond reclaim band.

### Swing High/Low Pools
Trigger:
- Major swing high/low (external structure) violated. [web:264][web:267][web:270][web:272]

Confirmation and kill mirror EQH/EQL: reversal vs run with clear reclaim/acceptance and symmetric kill rules.

---

## RTS-CHOCH — Structure Flip

### Mission
Interpret when a liquidity event actually flips market structure (change of character) rather than just tagging stops, using BOS and CHOCH signals from structure engines like DeltaSR. [web:143][web:235][file:141]

### Inputs
- BOS/CHOCH flags from structure tools (ShowBOS/ShowCHOCH). [file:141]
- Liquidity context from RTS-LIQ.
- Trend context from ST-AI and MA ribbon.
- Volume/delta context from DeltaSR (delta_bias, VPOC/VAH/VAL/HVN). [file:141]

### Triggers
- LIQ sweep at a major pool (EQH/EQL, PDH/PDL, swing pool, breakout shelf).
- Followed by a BOS in the opposite direction within a defined bar window.
- CHOCH flag raised by the structure engine at or after the sweep. [web:143][web:235][file:141]

### Confirmation
- flip_confirmed when:
  - liquidity sweep is validated (RTS-LIQ trap or run identified),
  - BOS confirms in the new direction,
  - CHOCH or MSS signal present,
  - delta/volume sponsorship supports the new side. [web:143][web:235][web:270][file:141]

### Kill Rules
- CHOCH thesis dies if:
  - BOS/CHOCH level fails to hold (price retakes old regime structure),
  - no follow-through within the CHOCH window,
  - trend/ribbon context reverts.
- kill_level anchored at CHOCH/BOS level plus tolerance.

### Intent
- ATTACK_TRAP when CHOCH confirms reversal after LIQ trap.
- ATTACK_BREAK when CHOCH confirms continuation after LIQ run.
- PROBE for partial CHOCH alignment.
- WAIT when structure flip signals are mixed.
- CUT when CHOCH/BOS invalidated.

---

## RTS-BOS — Continuation via BOS Retest

### Mission
Trade continuation after a break of structure using retest entries, as encoded by DeltaSR, but governed by RTS-LIQ and RTS-CHOCH context. [web:143][web:234][file:141]

### Inputs
- BOSLevel structures from DeltaSR (price, isBullish, bosBar, retestDone, active). [file:141]
- BOSRetestBars and RetestTolerance configuration. [file:141]
- Liquidity sweep data (RTS-LIQ).
- CHOCH status (RTS-CHOCH).
- Trend and ribbon context.
- Delta/volume context.

### Triggers
- Valid BOS event: swing high/low broken in direction of bias. [web:143][web:234][file:141]
- Retest within BOSRetestBars and RetestTolerance. [file:141]
- Optional confluence with nearby liquidity pools and unmitigated zones.

### Confirmation
- retest_valid when:
  - price tags BOS level within tolerance,
  - rejection in direction of BOS,
  - delta_min_pct threshold satisfied if RequireDelta is true. [file:141]

### Kill Rules
- BOS continuation thesis dies if:
  - retest fails (price slices through BOS and holds on the wrong side),
  - BOS level is reclaimed against the thesis,
  - retest window expires without valid test.
- kill_level defined around BOS level with tolerance.

### Intent
- ATTACK_BREAK when BOS retest is valid and aligned with LIQ/CHOCH/trend.
- PROBE for marginal BOS retests with weaker context.
- WAIT when BOS exists but retest not yet seen.
- CUT when BOS continuation invalidated.

---

## RTS-ZONE — Unmitigated Inventory

### Mission
Treat unmitigated zones (support/resistance, order-block-like areas) as finite inventory: first touch tradable; second touch kills future use, mirroring DeltaSR's zone rules. [file:141]

### Inputs
- UnmitZone structures from DeltaSR (top, bottom, price, isResistance, mitigated, createdTime, createdBar). [file:141]
- HideWhenMitigated, MaxZones, DrawZoneBoxes config. [file:141]
- Liquidity and BOS context.

### Triggers
- Fresh unmitigated zone created after swing structure event.
- First return of price into the zone (PriceInZone helper). [file:141]

### Confirmation
- zone_touch_valid when:
  - first touch respects zone boundaries,
  - structure and BOS context support zone polarity,
  - delta/volume do not contradict the zone thesis.

### Kill Rules
- zone mitigated (second touch) marks zone as dead; HideWhenMitigated removes it from the chart. [file:141]
- RTS-ZONE inherits this: no future trades from a mitigated zone; auto-cut any zone-based idea on second touch or explicit mitigation.
- kill_level derived from zone top/bottom plus tolerance.

### Intent
- ATTACK (zone-first-touch) when all context aligns.
- PROBE when context is weaker.
- CUT and IGNORE for mitigated or second-touch zones.

---

## RTS-DELTA — Sponsorship & Volume Profile

### Mission
Confirm whether buyers or sellers are sponsoring LIQ/CHOCH/BOS/ZONE ideas, using delta ratios and volume profile (VPOC, VAH, VAL, HVN) from DeltaSR. [file:141]

### Inputs
- VolumeProfile struct (vpoc, vah, val, hnv[], hnvCount, buyVol, sellVol, totalDelta). [file:141]
- DeltaMinPct and RequireDelta settings. [file:141]

### Fields
- delta_bias (BUY_DOMINANT / SELL_DOMINANT / NEUTRAL).
- sponsorship_quality (HIGH / MEDIUM / LOW).
- vp_context (PRICE_ABOVE_VPOC / PRICE_AT_VPOC / PRICE_BELOW_VPOC).

### Rules
- delta_bias: BUY_DOMINANT when buyVol / (buyVol+sellVol) >= DeltaMinPct; SELL_DOMINANT when sellVol / total >= DeltaMinPct; else NEUTRAL. [file:141]
- sponsorship_quality: HIGH when delta_bias aligns with RTS intent and totalDelta large; LOW when misaligned or small.
- vp_context interprets price location relative to VPOC/VAH/VAL.

### Impact on Intent
- RTS-DELTA boosts Offence when sponsorship_quality HIGH and aligned.
- RTS-DELTA boosts Defence (or downgrades intent) when misaligned.
- If RequireDelta is true and delta_bias is misaligned, RTS may choose PROBE/WAIT instead of ATTACK.

---

## Shared RTS Fields

Every RTS branch populates a shared envelope:
- rts_family (LIQ / CHOCH / BOS / ZONE / DELTA)
- offence_score
- defence_score
- trap_score
- intent
- kill_level
- auto_cut

This envelope feeds into the scanner, signal bus, alerts, and live feed. The existing architecture already supports structured fields and action_state; RTS extends this with family and intent, keeping the feed simple while backend logic grows richer. [file:94][file:96][file:97]

