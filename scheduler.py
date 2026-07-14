# scheduler.py — JHL Holdings loop engine
import subprocess, time, logging, os, json, threading
import urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scheduler")

MODULE_DIR = Path(__file__).resolve().parent
SCANNER        = MODULE_DIR / "tak_scanner_v3.py"
RTS_SNIPER     = MODULE_DIR / "rts_sniper.py"
RTS_INTERVAL   = 10 * 60   # RTS sniper runs every 10 min
RTS_TIMEOUT    = 300        # 5 min max per RTS cycle
PYTHON = "python3"  # Windows: adjust if needed
INTERVAL_SECONDS = 20 * 60  # 20 minutes
ACTIVE_START_HOUR = 5   # 5 AM CDT
ACTIVE_END_HOUR = 22    # 10 PM CDT
TIMEOUT = 480           # 8 min max per scan (parallel fetches ~30-60s)

# Write directly to CF KV via REST API — bypasses WAF on the Worker URL
CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID   = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN  = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL     = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
SIGNAL_BUS    = MODULE_DIR / "signal_bus.json"


def is_active_window():
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("America/Chicago"))
    except Exception:
        from datetime import timezone, timedelta
        now = datetime.utcnow().replace(tzinfo=timezone.utc) - timedelta(hours=5)
        now = now.replace(tzinfo=None)
    return ACTIVE_START_HOUR <= now.hour < ACTIVE_END_HOUR


def push_to_cf():
    """Write signal_bus.json directly to CF KV via REST API (bypasses Worker WAF)."""
    if not SIGNAL_BUS.exists():
        logger.warning("push_to_cf: signal_bus.json not found — skipping")
        return
    try:
        payload = SIGNAL_BUS.read_bytes()
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
            status = resp.status
        logger.info("CF KV push OK — HTTP %s", status)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        logger.error("CF KV push HTTP error: %s %s — %s", e.code, e.reason, body)
    except Exception as e:
        logger.error("CF KV push failed: %s", e)


def run_scan():
    if not SCANNER.exists():
        logger.error("Scanner not found: %s", SCANNER)
        return
    try:
        logger.info("Running scan: %s", SCANNER.name)
        result = subprocess.run(
            [PYTHON, str(SCANNER)],
            cwd=str(MODULE_DIR),
            timeout=TIMEOUT,
            capture_output=True,
            text=True
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        if result.stderr:
            logger.warning(result.stderr.strip()[:500])
    except KeyboardInterrupt:
        raise  # let the outer loop catch it cleanly
    except subprocess.TimeoutExpired:
        logger.error("Scan timed out after %ds", TIMEOUT)
    except Exception as e:
        logger.error("Scan failed: %s", e)


def run_rts_sniper():
    """Run RTS Sniper in a background thread — non-blocking."""
    if not RTS_SNIPER.exists():
        logger.error("RTS Sniper not found: %s", RTS_SNIPER)
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


if __name__ == "__main__":
    logger.info("JHL Scheduler starting. Interval: %d min. Window: %d-%d CDT",
                INTERVAL_SECONDS // 60, ACTIVE_START_HOUR, ACTIVE_END_HOUR)
    _rts_tick = 0   # counts 20-min intervals; sniper runs every 10 min
    while True:
        if is_active_window():
            # Main scanner — every 20 min
            run_scan()
            push_to_cf()
            # RTS Sniper — parallel thread every 10 min
            # (fires on every loop; 20-min scanner gives it overlap)
            rts_thread = threading.Thread(
                target=run_rts_sniper, daemon=True, name="rts-sniper"
            )
            rts_thread.start()
            logger.info("RTS Sniper thread launched (daemon)")
        else:
            logger.info("Outside active window — sleeping")
        time.sleep(INTERVAL_SECONDS)
