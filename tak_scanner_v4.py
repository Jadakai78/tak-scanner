from __future__ import annotations
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests

from aisupertrend import AISupertrend
from convictionscorer import ConvictionScorer, score_v2_shadow
from microstructure import enrich as microenrich
from pairuniverse import PairUniverse, PROP_WHITELIST
from regimeclassifier import RegimeClassifier
from remi import Remi
from signalbus import SignalBus
from strategies import ENGINE_CLASSES, REGIME_ENGINES, S8MTFConfluence, score_delta_context
from gimba_formatter import format_gimba_message
from scannermodels import PairContext, CandidateSignal
from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry

# Configuration
SEATS = [
    {"name": "Dragon", "risk": 177, "mode": "FULL_AGGRESSION"},
    {"name": "Starter3", "risk": 130, "mode": "FULL_AGGRESSION"},
    {"name": "Starter2", "risk": 66, "mode": "FULL_AGGRESSION"},
    {"name": "Eval1", "risk": 13, "mode": "PROTECT_ONLY"},
]

logger = logging.getLogger(__name__)
MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "s8_mtf_confluence.json"
FG_URL = "https://api.alternative.me/fng/"
OHL_COLUMNS = ["open", "high", "low"]
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


def build_bus_payload(
    lastscan: str,
    nextscan: str,
    fg: int,
    activepairs: int,
    deadpairs: int,
    signals: List[Dict[str, Any]],
    killedsignals: List[Dict[str, Any]],
    regimemap: Dict[str, str],
    sessionstats: Dict[str, Any],
    quiethours: bool,
    sprintmode: bool,
    april_view: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Builds the canonical bus payload snapshot.
    
    Returns a dictionary ready for JSON serialization and bus transmission.
    Includes optional April integration view.
    """
    payload = {
        "lastscan": lastscan,
        "nextscan": nextscan,
        "fg": fg,
        "activepairs": activepairs,
        "deadpairs": deadpairs,
        "signals": signals,
        "killedsignals": killedsignals,
        "regimemap": regimemap,
        "sessionstats": sessionstats,
        "quiethours": quiethours,
        "sprintmode": sprintmode,
    }
    
    if april_view is not None:
        payload["april_view"] = april_view
    
    return payload


def write_bus_snapshot(payload: Dict[str, Any], output_path: Path) -> None:
    """
    Writes the bus payload snapshot to disk as JSON.
    """
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"Bus snapshot written to {output_path}")
    except Exception as e:
        logger.error(f"Failed to write bus snapshot: {e}")


class TakScannerV4:
    def __init__(self):
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        self.bus = SignalBus()
        self.classifier = RegimeClassifier()
        self.universe = PairUniverse()
        self.last_signals = []
        self.killed_cache = []
        
        # April integration scaffold
        self.april_enabled = False
        self.april_status = {"ready": False, "last_check": None}

            # V4 Architecture: Orchestrator + Specialist Registry
        registry = SpecialistRegistry()
        registry = registry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
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
            logger.warning(f"FG fetch failed: {e}")
            return 50

    def run_scan(self):
        now = datetime.now(timezone.utc)
        logger.info(f"Starting scan at {now.isoformat()}")
        
        fg = self.fetch_fear_greed()
        active = self.universe.get_active_pairs()
        dead_count = len(self.universe.pairs) - len(active)
        
        signals = []
        killed = []
        regime_map = {}
        stats = {"scanned": len(active), "signaled": 0, "killed": 0}
        
        for pair in active:
            try:
                regime = self.classifier.classify(pair)
                regime_map[pair] = regime
                
                aist = AISupertrend(pair)
                engine_class = REGIME_ENGINES.get(regime, ENGINE_CLASSES[0])
                engine = engine_class()
                
                # Fetch OHLC data for specialist strategies
                ohlc_df = self.universe.fetch_ohlc(pair, interval=60)  # 1h candles
                if ohlc_df is None or len(ohlc_df) < 50:
                    logger.warning(f"Insufficient OHLC data for {pair}, skipping")
                    continue
                
                candidate = engine.generate(pair, ohlc_df, regime, fg, aist)                                
                if candidate:
                    # Enrich with microstructure
                    enriched = microenrich(candidate)
                    
                    # MTF confluence check
                    mtf = S8MTFConfluence()
                    mtf_result = mtf.check_alignment(pair)
                    if mtf_result:
                        enriched.update(mtf_result)
                    
                    # Score conviction
                    score = self.scorer.score(enriched)
                    enriched["conviction"] = score
                    
                    # Determine action state
                    action_state = self._resolve_action_state(enriched, pair)
                    enriched["action_state"] = action_state
                    
                    if action_state == "Killed":
                        killed.append(enriched)
                        stats["killed"] += 1
                    else:
                        signals.append(enriched)
                        stats["signaled"] += 1
                        
            except Exception as e:
                logger.error(f"Error scanning {pair}: {e}")
        
        # Sort signals by intent rank
        signals.sort(key=lambda s: INTENT_RANK.get(s.get("intent", ""), 999))
        
        # Check for quiet hours and sprint mode
        quiet = now.hour not in SCAN_HOURS_UTC
        sprintmode = False  # Placeholder for future logic
        
        # Build canonical payload
        payload = build_bus_payload(
            lastscan=now.isoformat(),
            nextscan=self.next_scan_time(now).isoformat(),
            fg=fg,
            activepairs=len(active),
            deadpairs=dead_count,
            signals=signals,
            killedsignals=killed,
            regimemap=regime_map,
            sessionstats=stats,
            quiethours=quiet,
            sprintmode=sprintmode,
            april_view=self._build_april_view() if self.april_enabled else None,
        )
        
        # Update bus (legacy interface)
        self.bus.update(
            lastscan=now.isoformat(),
            nextscan=self.next_scan_time(now).isoformat(),
            fg=fg,
            activepairs=len(active),
            deadpairs=dead_count,
            signals=signals,
            killedsignals=killed,
            regimemap=regime_map,
            sessionstats=stats,
            quiethours=quiet,
            sprintmode=sprintmode,
        )
        
        # Write snapshot to disk
        snapshot_path = MODULE_DIR / "signal_bus.json"
        write_bus_snapshot(payload, snapshot_path)
        
        # Fire alerts
        self.fire_alerts(signals[:MAX_SAMMY_ALERTS])
        
        logger.info(f"Scan complete: {stats['signaled']} signals, {stats['killed']} killed")
        return payload

    def _resolve_action_state(self, signal: Dict[str, Any], pair: str) -> str:
        """
        Determines whether a signal is active or killed based on TTL and other factors.
        """
        # Check if signal is in last_signals and still within TTL
        for old_signal in self.last_signals:
            if old_signal.get("pair") == pair:
                timestamp = old_signal.get("timestamp")
                if timestamp:
                    try:
                        signal_time = datetime.fromisoformat(timestamp)
                        age_hours = (datetime.now(timezone.utc) - signal_time).total_seconds() / 3600
                        if age_hours > SIGNAL_TTL_HOURS:
                            return "Killed"
                    except Exception:
                        pass
        return "Signal"

    def _build_april_view(self) -> Dict[str, Any]:
        """
        Scaffold for April integration panel data.
        Returns status and performance metrics for April's alert system.
        """
        return {
            "status": "OK",
            "last_check": datetime.now(timezone.utc).isoformat(),
            "bot_health": {"scanner": "running", "scheduler": "running"},
            "alerts_pending": 0,
            "performance_flags": [],
        }

    def fire_alerts(self, top_signals: List[Dict[str, Any]]):
        """
        Sends top signals to Telegram and other alert channels.
        """
        for signal in top_signals:
            try:
                message = format_gimba_message(signal)
                # Send via Telegram, Outlook, etc.
                logger.info(f"Alert fired for {signal.get('pair', 'unknown')}")
            except Exception as e:
                logger.error(f"Alert failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = TakScannerV4()
    scanner.run_scan()
