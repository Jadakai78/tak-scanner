from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from convictionscorer import ConvictionScorer
from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier
from remi import Remi
from signalbus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES
from gimba_formatter import format_gimba_message
from scannermodels import PairContext
from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry
from oracle_schema import (
    OracleHealth,
    OraclePayload,
    OracleSummary,
    payload_from_actions,
    make_oracle_action,
)

SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

PROPS_PAIRS = {
    "BTC", "ETH", "SOL", "HYPE", "XRP", "ZEC", "SUI", "ADA", "DOGE", "AAVE",
    "LTC", "TAO", "LINK", "UNI", "NEAR", "ARB", "ONDO", "TRX", "AVAX", "DOT",
    "BCH", "PUMP", "CRV", "ALGO", "TIA", "HBAR", "WLD", "FARTCOIN", "POL", "XPL",
    "WIF", "BNB", "INJ", "FIL", "JUP", "ATOM", "LDO", "PENGU", "VIRTUAL", "RENDER",
    "JTO", "GRASS", "KAITO", "TRUMP", "ASTER", "OP", "POPCAT", "APT", "S", "STX",
    "ETC", "MOODENG", "PNUT", "AIXBT",
}

logger = logging.getLogger(__name__)
MODULE_DIR = Path(__file__).resolve().parent
FG_URL = "https://api.alternative.me/fng/"
SCAN_HOURS_UTC = [14, 18, 22, 2, 6, 10]
SCAN_MINUTE_UTC = 0
INTENT_RANK = {
    "EVICTION_NOTICE": 1,
    "POWER_PLAY": 2,
    "STRUCTURE_BREAK": 3,
    "S_A_DELTA": 4,
    "B_DELTA": 5,
    "STARTER_DELTA": 6,
}

MAX_SAMMY_ALERTS = 5
SIGNAL_TTL_HOURS = 48
BUS_PATH = Path("/app/data/signal_bus.json") if Path("/app/data").exists() else MODULE_DIR / "signal_bus.json"


def write_bus_snapshot(payload: Dict[str, Any], output_path: Path) -> None:
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info("Bus snapshot written to %s", output_path)
    except Exception as e:
        logger.error("Failed to write bus snapshot: %s", e)
        raise


class TakScannerV4:
    def __init__(self):
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        self.bus = SignalBus(path=BUS_PATH)
        self.classifier = RegimeClassifier()
        self.universe = PairUniverse()
        self.last_signals: List[Dict[str, Any]] = []

        try:
            registry = SpecialistRegistry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
        except Exception:
            registry = SpecialistRegistry()
            try:
                registry = registry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
            except Exception as e:
                logger.exception("Failed to build SpecialistRegistry from engine map: %s", e)
                registry = SpecialistRegistry()

        self.orchestrator = ScannerOrchestrator(specialist_registry=registry)

    def next_scan_time(self, now: datetime) -> datetime:
        candidates = []
        for hour in SCAN_HOURS_UTC:
            candidate = now.replace(hour=hour, minute=SCAN_MINUTE_UTC, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            candidates.append(candidate)
        return min(candidates)

    def fetch_fear_greed(self) -> int:
        try:
            resp = requests.get(FG_URL, timeout=10)
            resp.raise_for_status()
            return int(resp.json()["data"][0]["value"])
        except Exception as e:
            logger.warning("FG fetch failed: %s", e)
            return 50

   def run_scan(self) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    logger.info("Starting Oracle-first scan at %s", now.isoformat())

    fg = self.fetch_fear_greed()
    fg_score = fg

    raw_active = self.universe.get_active_pairs()
    active = [
        item for item in raw_active
        if isinstance(item, dict) and item.get("pair") in PROPS_PAIRS
    ]
    dead_count = max(len(getattr(self.universe, "pairs", [])) - len(active), 0)

    regime_map: Dict[str, str] = {}
    contexts: List[PairContext] = []

    logger.info("Props filter kept %s of %s active pairs", len(active), len(raw_active))
    logger.info("Oracle building contexts for %s active pairs", len(active))

    for item in active:
        pair = item.get("pair")
        rows = item.get("ohlc_4h", [])

        if not pair:
            logger.warning("Skipping active item with no pair: %s", item)
            continue
        if not rows:
            logger.warning("Skipping %s due to missing ohlc_4h", pair)
            continue

        try:
            ohlc_df = pd.DataFrame(
                rows,
                columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
            )
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                ohlc_df[col] = pd.to_numeric(ohlc_df[col], errors="coerce")
            ohlc_df["time"] = pd.to_numeric(ohlc_df["time"], errors="coerce")
            ohlc_df = ohlc_df.dropna().reset_index(drop=True)

            regime = self.classifier.classify(pair, ohlc_df, fg_score)
        except Exception as e:
            logger.warning("Skipping %s due to regime prep failure: %s", pair, e)
            continue

        regime_map[pair] = regime
        contexts.append(
            PairContext(
                pair=pair,
                market_regime=regime,
                timeframe="1h",
                fear_greed=fg,
                session=None,
                indicators={},
                market_state={},
            )
        )

    shared_state = {"fgscore": fg, "timeframe": "1h"}
    logger.info("Oracle calling orchestrator with %s contexts", len(contexts))
    candidates = self.orchestrator.run(contexts, shared_state)
    logger.info("Oracle orchestrator returned %s candidates", len(candidates))

    oracle_actions = []
    action_priority = {"signal": 0, "caution": 1, "kill": 2, "flat": 3}

    for candidate in candidates:
        action_type = self._candidate_action(candidate)
        reason = (getattr(candidate, "thesis", "") or "").strip() or "Oracle setup recognized"

        oracle_action = make_oracle_action(
            pair=getattr(candidate, "pair", "UNKNOWN"),
            action=action_type,
            timestamp=now.isoformat(),
            setup_family=getattr(candidate, "setup_type", None),
            side=getattr(candidate, "side", None),
            confidence=float(getattr(candidate, "confidence", 0.0) or 0.0),
            score=float(getattr(candidate, "score", 0.0) or 0.0),
            why_now=reason[:220],
            entry_idea=getattr(candidate, "entry_idea", None),
            stop_idea=getattr(candidate, "stop_idea", None),
            target_idea=getattr(candidate, "target_idea", None),
            tags=list(getattr(candidate, "tags", []) or []),
            warnings=list(getattr(candidate, "warnings", []) or []),
            context={
                "regime": regime_map.get(getattr(candidate, "pair", ""), "UNKNOWN"),
                "fg": fg,
                "specialist": getattr(candidate, "specialist", None),
                "intent": getattr(candidate, "intent", None),
                "grade": getattr(candidate, "grade", None),
            },
        )
        oracle_actions.append(oracle_action)

    oracle_actions.sort(
        key=lambda a: (
            action_priority.get(a.action, 99),
            -float(a.score or 0.0),
            -float(a.confidence or 0.0),
        )
    )

    filtered_actions = [
        a for a in oracle_actions
        if a.action != "signal" or float(a.score or 0.0) >= 0.70
    ]

    fg_obj = {"score": fg, "label": self._fg_label(fg)}

    summary = OracleSummary(
        fg=fg,
        fg_label=fg_obj["label"],
        market_phase=self._market_phase(fg, len([a for a in filtered_actions if a.action == "signal"])),
        session=self._get_session(now),
        regime_summary=self._regime_summary(fg, regime_map, filtered_actions),
        active_pairs=len(active),
        dead_pairs=dead_count,
        scan_mode="scheduled",
    )

    health = OracleHealth(
        scheduler_ok=True,
        bus_ok=True,
        publish_ok=True,
        last_error=None,
        source_path=str(self.bus.path),
        heartbeat=now.isoformat(),
    )

    payload_obj: OraclePayload = payload_from_actions(
        last_scan=now.isoformat(),
        next_scan=self.next_scan_time(now).isoformat(),
        oracle=summary,
        actions=filtered_actions,
        positions=[],
        health=health,
    )

    payload = payload_obj.to_dict()

    payload["lastscan"] = payload["last_scan"]
    payload["nextscan"] = payload["next_scan"]
    payload["fg"] = fg_obj
    payload["f_g"] = fg_obj
    payload["activepairs"] = len(active)
    payload["active_pairs"] = len(active)
    payload["deadpairs"] = dead_count
    payload["dead_pairs"] = dead_count
    payload["regimemap"] = regime_map
    payload["regime_map"] = regime_map
    payload["sessionstats"] = {
        "scanned": len(active),
        "actions": len(filtered_actions),
        "signals": len(payload.get("signals", [])),
        "killed": len(payload.get("killedsignals", [])),
    }
    payload["quiethours"] = now.hour not in SCAN_HOURS_UTC
    payload["quiet_hours"] = payload["quiethours"]
    payload["sprintmode"] = False
    payload["sprint_mode"] = False

    self._trim_last_signals()
    self.last_signals = payload.get("signals", [])

    write_bus_snapshot(payload, self.bus.path)
    self.fire_alerts(payload.get("signals", [])[:MAX_SAMMY_ALERTS])

    logger.info(
        "Oracle scan complete: %s actions | %s signals | %s killed",
        len(filtered_actions),
        len(payload.get("signals", [])),
        len(payload.get("killedsignals", [])),
    )
    return payload
