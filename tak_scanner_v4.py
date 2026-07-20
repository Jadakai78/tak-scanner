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
        # Use Path.open for consistency
        with output_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"Bus snapshot written to {output_path}")
    except Exception as e:
        logger.error(f"Failed to write bus snapshot: {e}")


class TakScannerV4:
    def __init__(self):
        self.scorer = ConvictionScorer()
        self.remi = Remi()
        # Ensure scanner writes to the underscore-named bus file scheduler expects
        self.bus = SignalBus(path=MODULE_DIR / "signal_bus.json")
        self.classifier = RegimeClassifier()
        self.universe = PairUniverse()
        self.last_signals = []
        self.killed_cache = []
        
        # April integration scaffold
        self.april_enabled = False
        self.april_status = {"ready": False, "last_check": None}

        # V4 Architecture: Orchestrator + Specialist Registry
        try:
            # Prefer a classmethod if the API provides it
            registry = SpecialistRegistry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
        except Exception:
            # Fallback to instance method if available
            registry = SpecialistRegistry()
            try:
                registry = registry.from_engine_map(ENGINE_CLASSES, REGIME_ENGINES)
            except Exception as e:
                logger.exception(f"Failed to build SpecialistRegistry from engine map: {e}")
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
        
        # V4 ORCHESTRATOR PATTERN: Build contexts for all pairs, run specialists -> REMI -> APRIL
        logger.info("V4 building contexts for %s active pairs", len(active))
        contexts = []
        for pair in active:
            regime = self.classifier.classify(pair)
            regime_map[pair] = regime
            context = PairContext(
                pair=pair,
                market_regime=regime,
                timeframe="1h",
                fear_greed=fg,
                session=None,
                indicators={},
                market_state={},
            )
            contexts.append(context)
        
        # Run orchestrator: specialists -> REMI -> APRIL
        shared_state = {"fgscore": fg, "timeframe": "1h"}
        logger.info("V4 calling orchestrator with %s contexts", len(contexts))
        candidates = self.orchestrator.run(contexts, shared_state)
        logger.info("V4 orchestrator returned %s candidates", len(candidates))
        
        # Convert CandidateSignal objects to dict format for backward compatibility
        for candidate in candidates:
            signal_dict = {
                "pair": candidate.pair,
                "setup_type": candidate.setup_type,
                "side": candidate.side,
                "specialist": candidate.specialist,
                "thesis": candidate.thesis,
                "score": candidate.score,
                "confidence": candidate.confidence,
                "entry_idea": candidate.entry_idea,
                "stop_idea": candidate.stop_idea,
                "target_idea": candidate.target_idea,
                "warnings": candidate.warnings,
                "tags": candidate.tags,
                "context": candidate.context,
                "conviction": candidate.score,
                "timestamp": now.isoformat(),
            }
            
            # Route based on council decision
            if candidate.council and candidate.council.route == "killed_signals":
                killed.append(signal_dict)
                stats["killed"] += 1
            else:
                signals.append(signal_dict)
                
        
        # trim last_signals to avoid unbounded growth
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=SIGNAL_TTL_HOURS)
            trimmed = []
            for s in self.last_signals:
                ts = s.get("timestamp")
                if not ts:
                    continue
                try:
                    t = datetime.fromisoformat(ts)
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    if t >= cutoff:
                        trimmed.append(s)
                except Exception:
                    # if parsing fails, drop the entry
                    continue
            self.last_signals = trimmed
        except Exception:
            logger.debug("Failed to trim last_signals")
        
        # Sort signals by intent rank
        signals.sort(key=lambda s: INTENT_RANK.get(s.get("intent", ""), 999))
        
        # Check for quiet hours and sprint mode
        quiet = now.hour not in SCAN_HOURS_UTC
        sprintmode = False  # Placeholder for future logic
        
        # Build april_view once so payload and live bus remain identical
        april_view = self._build_april_view() if self.april_enabled else None

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
            april_view=april_view,
        )
        
        # Update bus (legacy interface)
        try:
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
                april_view=april_view,
            )
        except Exception as e:
            logger.debug(f"Bus update_failed: {e}")
        
        # Write snapshot to disk
        snapshot_path = self.bus.path
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
                if not timestamp:
                    continue
                try:
                    signal_time = datetime.fromisoformat(timestamp)
                    # Ensure timezone-aware (assume UTC if none specified)
                    if signal_time.tzinfo is None:
                        signal_time = signal_time.replace(tzinfo=timezone.utc)
                    age_hours = (datetime.now(timezone.utc) - signal_time).total_seconds() / 3600
                    if age_hours > SIGNAL_TTL_HOURS:
                        return "Killed"
                except Exception as e:
                    logger.debug(f"Failed to parse timestamp for TTL check: {timestamp!r}: {e}")
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
                logger.exception(f"Alert failed: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scanner = TakScannerV4()
    scanner.run_scan()
