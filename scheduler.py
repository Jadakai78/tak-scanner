# scheduler.py — JHL Holdings loop engine
import subprocess, time, logging, json, threading
import urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scheduler")

MODULE_DIR       = Path(__file__).resolve().parent
SCANNER          = MODULE_DIR / "tak_scanner_v4.py"
RTS_SNIPER       = MODULE_DIR / "rts_sniper.py"
RTS_INTERVAL     = 10 * 60
RTS_TIMEOUT      = 300
PYTHON           = "python3"
INTERVAL_SECONDS = 20 * 60
TIMEOUT          = 600

CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID   = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN  = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL     = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
)

SIGNAL_BUS = MODULE_DIR / "signal_bus.json"
SIGNAL_BUS.parent.mkdir(parents=True, exist_ok=True)


def _signal_identity(sig: dict) -> tuple:
    """Strong identity for a signal instance."""
    return (
        (sig.get("pair") or "").strip(),
        (sig.get("bias") or "").strip(),
        (sig.get("engine") or sig.get("strategy") or sig.get("specialist") or "").strip(),
        (sig.get("fired_at") or sig.get("timestamp") or "").strip(),
    )


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def push_to_cf():
    """Write signal_bus.json directly to CF KV via REST API."""
    candidates = [
        Path("/app/data/signal_bus.json"),
        MODULE_DIR / "signal_bus.json",
        MODULE_DIR / "signalbus.json",
    ]

    bus_path = None
    for p in candidates:
        try:
            if p.exists():
                bus_path = p
                break
        except Exception:
            continue

    if not bus_path:
        logger.warning(
            "push_to_cf: no signal bus file found in canonical locations (%s) — skipping",
            ", ".join(str(x) for x in candidates),
        )
        return

    logger.info("push_to_cf: using signal bus file: %s", bus_path)
    try:
        payload = bus_path.read_bytes()
        req = urllib.request.Request(
            CF_KV_URL,
            data=payload,
            method="PUT",
            headers={
                "Authorization": f"Bearer {CF_API_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("CF KV push OK — HTTP %s", resp.status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        logger.error("CF KV push HTTP error: %s %s — %s", e.code, e.reason, body)
    except Exception as e:
        logger.error("CF KV push failed: %s", e)


def _snapshot_manual_verdicts() -> dict:
    """
    Save only intentional human-set verdicts, keyed by strong signal identity.
    This avoids smearing old WAIT/REJECT states onto fresh signals for the same pair.
    """
    saved = {}
    if not SIGNAL_BUS.exists():
        return saved

    old_bus = _load_json(SIGNAL_BUS)
    for sig in old_bus.get("signals", []):
        verdict = sig.get("december_verdict")
        if verdict and verdict not in ("PENDING", "", None):
            ident = _signal_identity(sig)
            saved[ident] = {
                "december_verdict": verdict,
                "rejected_at": sig.get("rejected_at"),
                "wait_at": sig.get("wait_at"),
                "executed_at": sig.get("executed_at"),
            }
    return saved


def _restore_manual_verdicts(saved_verdicts: dict):
    """
    Restore only exact signal-instance matches.
    Fallback pair-only restore is intentionally removed to prevent stale carryover.
    """
    if not saved_verdicts or not SIGNAL_BUS.exists():
        return

    new_bus = _load_json(SIGNAL_BUS)
    restored = []

    for sig in new_bus.get("signals", []):
        ident = _signal_identity(sig)
        if ident in saved_verdicts:
            sig.update({k: v for k, v in saved_verdicts[ident].items() if v is not None})
            restored.append({
                "pair": sig.get("pair"),
                "engine": sig.get("engine") or sig.get("strategy") or sig.get("specialist"),
                "fired_at": sig.get("fired_at") or sig.get("timestamp"),
                "verdict": sig.get("december_verdict"),
            })

    SIGNAL_BUS.write_bytes(json.dumps(new_bus, ensure_ascii=False, indent=2).encode())
    if restored:
        logger.info("Verdicts restored for exact matches only: %s", restored)
    else:
        logger.info("No exact verdict matches restored; fresh scan kept intact.")


def run_scan():
    if not SCANNER.exists():
        logger.error("Scanner not found: %s", SCANNER)
        return

    try:
        saved_verdicts = {}
        try:
            saved_verdicts = _snapshot_manual_verdicts()
            if saved_verdicts:
                logger.info("Saved %d manual verdict(s) before scan", len(saved_verdicts))
        except Exception as ve:
            logger.warning("Verdict snapshot failed: %s", ve)

        logger.info("Running scan: %s", SCANNER.name)
        result = subprocess.run(
            [PYTHON, str(SCANNER)],
            cwd=str(MODULE_DIR),
            timeout=TIMEOUT,
            capture_output=True,
            text=True,
        )

        if result.stdout:
            logger.info(result.stdout.strip()[:1500])
        if result.stderr:
            logger.warning(result.stderr.strip()[:800])

        try:
            _restore_manual_verdicts(saved_verdicts)
        except Exception as re:
            logger.warning("Verdict restore failed: %s", re)

    except KeyboardInterrupt:
        raise
    except subprocess.TimeoutExpired:
        logger.error("Scan timed out after %ds", TIMEOUT)
    except Exception as e:
        logger.error("Scan failed: %s", e)
    finally:
        push_to_cf()


def run_rts_sniper():
    if not RTS_SNIPER.exists():
        logger.warning("RTS Sniper not found — skipping")
        return
    try:
        logger.info("RTS Sniper cycle starting")
        result = subprocess.run(
            [PYTHON, str(RTS_SNIPER)],
            cwd=str(MODULE_DIR),
            timeout=RTS_TIMEOUT,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.info("[RTS] %s", result.stdout.strip()[:400])
        if result.stderr:
            logger.warning("[RTS ERR] %s", result.stderr.strip()[:300])
    except KeyboardInterrupt:
        raise
    except subprocess.TimeoutExpired:
        logger.error("RTS Sniper timed out after %ds", RTS_TIMEOUT)
    except Exception as exc:
        logger.error("RTS Sniper failed: %s", exc)


def run():
    """Main loop — Railway daemon loop, 24/7."""
    logger.info(
        "JHL Scheduler starting 24/7. Scanner: %s | Interval: %d min",
        SCANNER.name, INTERVAL_SECONDS // 60,
    )
    while True:
        run_scan()

        rts_thread = threading.Thread(target=run_rts_sniper, daemon=True, name="rts-sniper")
        rts_thread.start()
        logger.info("RTS Sniper thread launched")

        try:
            if SIGNAL_BUS.exists():
                bus = _load_json(SIGNAL_BUS)
                now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                next_dt = datetime.now(timezone.utc) + timedelta(seconds=INTERVAL_SECONDS)
                bus["next_scan"] = next_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
                bus["scanner_heartbeat"] = now_iso
                bus["worker_push_ok"] = True
                bus["bus_write_ok"] = True
                SIGNAL_BUS.write_text(json.dumps(bus, ensure_ascii=False, indent=2))
        except Exception as se:
            logger.warning("Bus stamp failed: %s", se)

        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
