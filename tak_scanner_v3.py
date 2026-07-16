from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from phasepath import apply_phasepath
from aisupertrend import AISupertrend
from convictionscorer import ConvictionScorer, score_v2_shadow
from microstructure import enrich as microenrich
from pairuniverse import PairUniverse, PROP_WHITELIST
from regimeclassifier import RegimeClassifier
from remi import Remi
from signalbus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES, S8MTFConfluence, score_delta_context
from gimba_formatter import format_gimba_message


SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tak_scanner_v3")

MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "config.json"
FG_URL = "https://api.alternative.me/fng/?limit=1"
MAX_SAMMY_ALERTS = 5
SIGNAL_TTL_HOURS = 4
OHL_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]
SCAN_HOURS_UTC = [3, 7, 11, 15, 19, 23]
SCAN_MINUTE_UTC = 45

INTENT_RANK = {
    "ATTACKTRAP": 0,
    "ATTACKBREAK": 1,
    "ATTACK": 2,
    "PROBE": 3,
    "WAIT": 4,
    "CUT": 5,
    "IGNORE": 6,
}


def compute_sizing(entry: Any, sl: Any) -> Dict[str, Dict[str, Any]]:
    try:
        risk_per_unit = abs(float(entry) - float(sl))
    except (TypeError, ValueError):
        risk_per_unit = 0.0

    sizing: Dict[str, Dict[str, Any]] = {}
    for seat in SEATS:
        if seat["mode"] == "PROTECT_ONLY" or risk_per_unit <= 0:
            sizing[seat["name"]] = {
                "units": 0,
                "dollar_risk": 0,
                "mode": seat["mode"],
            }
            continue

        units = round(seat["risk"] / risk_per_unit, 2)
        sizing[seat["name"]] = {
            "units": units,
            "dollar_risk": round(units * risk_per_unit, 2),
            "mode": seat["mode"],
        }
    return sizing


def send_sammy_message(message: str) -> None:
    token = "8860741830:AAGiccCbk4dzoTq97gWIIykZVunDvkkl6ys"
    chat_id = "7733126931"
    try:
    
