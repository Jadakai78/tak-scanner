"""cf_publish.py — direct Cloudflare KV publisher for JHL signal bus.

Drop this file into jhl_v2 and call publish_signal_bus() immediately after
signal_bus.json is written by your scanner/scheduler path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests

CF_WORKER_URL = "https://giving-wisdom-production-9b27.up.railway.app/update"
CF_SECRET = "jhl2026dragon"
DEFAULT_TIMEOUT = 12

ACCOUNTS = [
    {"account_id": "eval_4_25k",    "name": "Eval 4 $25K DRAGON",  "recommended_risk_per_trade": 177.0},
    {"account_id": "starter_3_10k", "name": "Starter 3 $10K",      "recommended_risk_per_trade": 130.0},
    {"account_id": "starter_2_10k", "name": "Starter 2 $10K",      "recommended_risk_per_trade":  66.0},
    {"account_id": "eval_1_5k",     "name": "Eval 1 $5K",          "recommended_risk_per_trade":  13.0},
]

logger = logging.getLogger("cf_publish")


def _build_accounts(data: Dict[str, Any]) -> list[Dict[str, Any]]:
    baselines = data.get("session_baselines", {}) or {}
    accounts = []
    for acct in ACCOUNTS:
        aid = acct["account_id"]
        baseline = baselines.get(aid, acct["recommended_risk_per_trade"])
        accounts.append({
            "account_id": aid,
            "name": acct["name"],
            "baseline": baseline,
            "current_equity": baseline,
            "recommended_risk_per_trade": acct["recommended_risk_per_trade"],
            "mode": "FULL_AGGRESSION",
        })
    return accounts


def build_publish_payload(signal_bus_path: str | Path) -> Dict[str, Any]:
    path = Path(signal_bus_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["accounts"] = _build_accounts(data)
    return data


def publish_payload(payload: Dict[str, Any], timeout: int = DEFAULT_TIMEOUT) -> bool:
    body = json.dumps(payload)
    r = requests.put(
        CF_WORKER_URL,
        headers={"X-JHL-Secret": CF_SECRET, "Content-Type": "application/json"},
        data=body,
        timeout=timeout,
    )
    r.raise_for_status()
    return True


def publish_signal_bus(signal_bus_path: str | Path, timeout: int = DEFAULT_TIMEOUT) -> bool:
    payload = build_publish_payload(signal_bus_path)
    ok = publish_payload(payload, timeout=timeout)
    logger.info("CF publish OK")
    return ok


def safe_publish_signal_bus(signal_bus_path: str | Path, timeout: int = DEFAULT_TIMEOUT) -> bool:
    try:
        return publish_signal_bus(signal_bus_path, timeout=timeout)
    except Exception as exc:
        logger.exception("CF publish failed: %s", exc)
        return False
