# scheduler.py — JHL Holdings loop engine
import subprocess, time, logging, os, json, threading
import urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scheduler")

MODULE_DIR     = Path(__file__).resolve().parent
SCANNER        = MODULE_DIR / "tak_scanner_v4.py"   # v4 — was v3
RTS_SNIPER     = MODULE_DIR / "rts_sniper.py"
RTS_INTERVAL   = 10 * 60
RTS_TIMEOUT    = 300
PYTHON         = "python3"
INTERVAL_SECONDS = 20 * 60   # 20 minutes
TIMEOUT           = 600       # 10 min max per scan
# 24/7 — no active window gate

CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID   = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN  = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL     = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
)
SIGNAL_BUS = Path("/app/data/signal_bus.json")
# Ensure volume dir exists
SIGNAL_BUS.parent.mkdir(parents=True, exist_ok=True)



def push_to_cf():
    """Write signal_bus.json directly to CF KV via REST API."""
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
            logger.info("CF KV push OK — HTTP %s", resp.status)
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
            # ── Save any manually-set verdicts before scan overwrites SIGNAL_BUS ──
            _saved_verdicts = {}
            try:
                if SIGNAL_BUS.exists():
                    _old = json.loads(SIGNAL_BUS.read_text())
                    for _s in _old.get("signals", []):
                        _v = _s.get("december_verdict")
                        if _v and _v not in ("PENDING", "", None):
                            _saved_verdicts[_s["pair"]] = {
                                "december_verdict": _v,
                                "rejected_at": _s.get("rejected_at"),
                                "wait_at": _s.get("wait_at"),
                            }
            except Exception as _ve:
                logger.warning("Verdict snapshot failed: %s", _ve)
                logger.info("Running scan: %s", SCANNER.name)
        result = subprocess.run(
            [PYTHON, str(SCANNER)],
            cwd=str(MODULE_DIR),
            timeout=TIMEOUT,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.info(result.stdout.strip())
        if result.stderr:
            logger.warning(result.stderr.strip()[:500])
        # v4 scanner pushes to CF itself, but we also push here as backup
            # ── Restore manually-set verdicts that the scan overwrote ──
            try:
                if _saved_verdicts and SIGNAL_BUS.exists():
                    _new = json.loads(SIGNAL_BUS.read_text())
                    for _sig in _new.get("signals", []):
                        _p = _sig.get("pair")
                        if _p in _saved_verdicts:
                            _sig.update(_saved_verdicts[_p])
                    SIGNAL_BUS.write_bytes(json.dumps(_new, ensure_ascii=False, indent=2).encode())
                    logger.info("Verdicts restored for: %s", list(_saved_verdicts))
            except Exception as _re:
                logger.warning("Verdict restore failed: %s", _re)
        push_to_cf()
    except KeyboardInterrupt:
        raise
    except subprocess.TimeoutExpired:
        logger.error("Scan timed out after %ds", TIMEOUT)
    except Exception as e:
        logger.error("Scan failed: %s", e)


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
    """Main loop — called by server.py daemon thread so Railway runs this
    without any local machine involvement. Runs 24/7, no quiet window."""
    logger.info(
        "JHL Scheduler starting 24/7. Scanner: %s | Interval: %d min",
        SCANNER.name, INTERVAL_SECONDS // 60,
    )
    while True:
        run_scan()
        rts_thread = threading.Thread(target=run_rts_sniper, daemon=True, name="rts-sniper")
        rts_thread.start()
        logger.info("RTS Sniper thread launched")
        # ── Stamp next_scan + worker_push_ok into local bus after each cycle ──         try:             if SIGNAL_BUS.exists():                 _bus = json.loads(SIGNAL_BUS.read_text())                 _next = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")                 from datetime import timedelta as _td                 _next_dt = datetime.utcnow() + _td(seconds=INTERVAL_SECONDS)                 _bus["next_scan"] = _next_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")                 _bus["scanner_heartbeat"] = _next                 _bus["worker_push_ok"] = True                 _bus["bus_write_ok"] = True                 SIGNAL_BUS.write_text(json.dumps(_bus, ensure_ascii=False, indent=2))         except Exception as _se:             logger.warning("Bus stamp failed: %s", _se)         time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
