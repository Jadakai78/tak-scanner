# SCANNER_WRITE_CONTRACT

Canonical writer contract for `/app/data/signal_bus.json` (Railway-only runtime source of truth).

## Top-level bus keys

Writers should preserve existing keys and update these canonical keys:

- `signals` (array, required): live + historical signal records.
- `rts_signals` (array, optional): RTS records if used by the scanner stack.
- `last_scan` (string, recommended): latest scanner timestamp in ISO-8601 UTC.
- `active_pairs` (number, optional): advisory only; server derives active pairs from normalized signal verdicts.
- `oracle` / `tak` objects (optional): scanner metadata.

## Required signal fields (canonical writer schema)

Each signal written to `signals[]` should include:

- `pair` (string) — market pair symbol (example: `BTCUSD`).
- `engine` (string) — engine/source name.
- `bias` (string) — directional bias.
- `score` (number) — conviction score (preferred canonical score key, `0-100` inclusive).
- `trap_risk` (number) — trap risk score.
- `fired_at` (string) — signal fired timestamp (ISO-8601 UTC).
- `verdict` (string) — one of `PENDING|CONFIRM|WAIT|REJECT|EXPIRED`.

For migration safety, writers may also include `december_verdict` mirrored to `verdict`.

## Timestamp and score rules

- Use ISO-8601 UTC timestamps (`YYYY-MM-DDTHH:MM:SS.sss+00:00` or `...Z`).
- `score` should be percent-style (`0..100`) for canonical writes.
- Reader compatibility in server supports legacy forms:
  - conviction from `score | conviction | final_conviction | confidence`.
  - verdict from `december_verdict | verdict | status | state`.
  - fired time from `fired_at | created_at | ts | timestamp`.

## Atomic write recommendation

To prevent partial reads/corruption during concurrent scanner + server access:

1. Write JSON to a unique temp file in the same directory (for example, `signal_bus.json.tmp.<pid>`, `signal_bus.json.tmp.<timestamp-us>`, or `signal_bus.json.tmp.<uuid>`).
2. `fsync` temp file if available in your runtime.
3. Rename temp file over `/app/data/signal_bus.json` (atomic replace on same filesystem).

This `tmp + rename` pattern keeps readers safe while writers update the bus.
